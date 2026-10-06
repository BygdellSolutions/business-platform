"""The outermost layer of the web process: the request id, the one safe request log line and the response headers.

Pure ASGI (not `BaseHTTPMiddleware`) so it never buffers or re-streams a response. It wraps EVERYTHING, including a
request the internal-secret check refuses, so a refused request is logged too.
"""

import logging
import re
import secrets
import time

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# The id is a correlation key, not a secret and not an authority. Its format is fixed and bounded so that nothing a
# client sends can put control characters or a huge string into a log: anything else is replaced.
REQUEST_ID_HEADER = "x-request-id"
REQUEST_ID_PATTERN = re.compile(rb"^[A-Za-z0-9_-]{16,64}$")
MAX_LOGGED_PATH = 200

# The backend answers the BFF, never a browser, so its headers are the minimal ones (the browser-facing set is the BFF's).
RESPONSE_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
}

_access = logging.getLogger("bp.access")


def new_request_id() -> str:
    return secrets.token_hex(16)


def request_id_from(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name == REQUEST_ID_HEADER.encode() and REQUEST_ID_PATTERN.fullmatch(value):
            return value.decode("ascii")
    return new_request_id()


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = request_id_from(scope)
        scope["bp_request_id"] = request_id
        started = time.perf_counter()
        status = 500  # what the client gets if the application fails before it answers

        async def send_with_headers(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
                for name, value in RESPONSE_HEADERS.items():
                    headers[name] = value
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        finally:
            _access.log(
                logging.ERROR if status >= 500 else logging.INFO,
                "request",
                extra={
                    "fields": {
                        "request_id": request_id,
                        "method": scope.get("method", ""),
                        "path": scope.get("path", "")[:MAX_LOGGED_PATH],  # the path only: the query string is never logged
                        "status": status,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    }
                },
            )
