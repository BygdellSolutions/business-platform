"""Outgoing email: one function, `send`, and two providers behind it.

  log         nothing leaves the machine. Used by development, the tests and a deployment that has no provider yet. The log line
              names the recipient count and the subject, never the text.
  cloudflare  Cloudflare Email Service over its REST API (POST /accounts/{id}/email/sending/send, Bearer token). The application is
              not a Worker, so the REST API is the right door; the sender domain must be onboarded for sending first.

`send` NEVER raises and never blocks a request for long: it returns a `SendResult` whose `code` is a short, safe word ("sent",
"not_configured", "rejected", "unavailable", ...) that can be stored and shown to an operator. The provider's answer text, the
token and the message body are never put in the result or in a log line. A caller that must not lose anything stores its record
FIRST and sends afterwards (see app/api/support.py).
"""

import base64
import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from app.core.config import settings
from app.core.logging_config import log

SEND_TIMEOUT_SECONDS = 8
API = "https://api.cloudflare.com/client/v4/accounts/{account}/email/sending/send"


@dataclass(frozen=True)
class Attachment:
    filename: str
    content: bytes
    type: str  # the media type, e.g. application/pdf


@dataclass(frozen=True)
class EmailMessage:
    to: list[str]
    subject: str
    text: str
    reply_to: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    attachments: list[Attachment] = field(default_factory=list)
    # The sender when it is not the platform's default: an address on a domain onboarded for sending, and the name people see.
    from_address: str | None = None
    from_name: str | None = None


# Deliberately plain: one "@", no spaces or quotes, a dot in the domain. Whether the address works is for the provider to say.
ADDRESS = re.compile(r"^[^@\s<>\",;]+@[^@\s<>\",;]+\.[^@\s<>\",;]{2,}$")
MAX_MESSAGE_BYTES = 5 * 1024 * 1024  # the provider's limit for a whole message; a bigger attachment is refused before it is sent


def looks_like_address(value: str) -> bool:
    return len(value) <= 254 and ADDRESS.fullmatch(value) is not None


@dataclass(frozen=True)
class SendResult:
    ok: bool
    code: str  # sent | queued | not_configured | rejected | unauthorized | rate_limited | unavailable


def _clean_header(value: str) -> str:
    """A header value is one line: a line break in a subject or address is how a header gets injected."""
    return " ".join(value.replace("\r", " ").replace("\n", " ").split())


def _payload(message: EmailMessage) -> dict:
    sender = _clean_header(message.from_address or settings.email_from)
    body: dict = {
        "from": {"address": sender, "name": _clean_header(message.from_name)[:100]} if message.from_name else sender,
        "to": [_clean_header(address) for address in message.to],
        "subject": _clean_header(message.subject)[:200],
        "text": message.text,
    }
    if message.reply_to:
        body["reply_to"] = _clean_header(message.reply_to)
    if message.attachments:
        body["attachments"] = [
            {"content": base64.b64encode(item.content).decode("ascii"), "filename": _clean_header(item.filename)[:150], "type": item.type, "disposition": "attachment"}
            for item in message.attachments
        ]
    if message.headers:
        body["headers"] = {_clean_header(name): _clean_header(value) for name, value in message.headers.items()}
    return body


def _classify(status: int) -> SendResult:
    if status in (401, 403):
        return SendResult(False, "unauthorized")  # a bad token, a token without the right, or sending not enabled for the domain
    if status == 429:
        return SendResult(False, "rate_limited")
    if 400 <= status < 500:
        return SendResult(False, "rejected")  # the request itself was refused: retrying the same message will not help
    return SendResult(False, "unavailable")


def _send_cloudflare(message: EmailMessage) -> SendResult:
    token = settings.email_api_token.get_secret_value() if settings.email_api_token else None
    if not settings.cloudflare_account_id or not token:
        return SendResult(False, "not_configured")
    request = urllib.request.Request(
        API.format(account=settings.cloudflare_account_id),
        data=json.dumps(_payload(message)).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=SEND_TIMEOUT_SECONDS) as response:  # noqa: S310 (a fixed https URL)
            answer = json.loads(response.read(65536) or b"{}")
    except urllib.error.HTTPError as error:
        return _classify(error.code)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return SendResult(False, "unavailable")
    if not isinstance(answer, dict) or answer.get("success") is not True:
        return SendResult(False, "rejected")
    result = answer.get("result") if isinstance(answer.get("result"), dict) else {}
    if result.get("permanent_bounces"):
        return SendResult(False, "rejected")  # the address does not exist: nothing was delivered
    return SendResult(True, "sent" if result.get("delivered") else "queued")


def send(message: EmailMessage) -> SendResult:
    """Send one message. Never raises."""
    try:
        if settings.email_provider == "cloudflare":
            outcome = _send_cloudflare(message)
        else:
            log(logging.INFO, "email_not_sent_log_provider", recipients=len(message.to), subject=_clean_header(message.subject)[:80])
            outcome = SendResult(False, "not_configured")
    except Exception:  # noqa: BLE001 (nothing a provider does may fail the request that asked for the email)
        outcome = SendResult(False, "unavailable")
    if not outcome.ok and outcome.code != "not_configured":
        log(logging.WARNING, "email_send_failed", code=outcome.code, recipients=len(message.to))
    return outcome
