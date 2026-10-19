"""The support page's message box: a public endpoint that stores a ticket first and emails afterwards.

Covers the ticket number, who is recognised (only the server decides), the spam defences (a trap field, per-address and per-source
limits, a global valve), the bounds on the text, what the two emails contain (and do not), what happens when the provider fails
(the ticket is kept and says why), and that erasing an account takes the person's words out of their tickets.
"""

import io
import json
import urllib.error

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import rate_limit, support
from app.core.config import settings
from app.models import SupportTicket
from app.scripts import admin
from tests.factories import make_user

MESSAGE = "I cannot find where to change the price of a service. Please help."
BODY = {"name": "Anna Andersson", "email": "anna@example.test", "message": MESSAGE}


@pytest.fixture(autouse=True)
def fresh_limits():
    rate_limit.reset()  # the per-source window lives in the process, shared by every test
    yield
    rate_limit.reset()


def send(client: TestClient, body: dict | None = None, headers: dict | None = None):
    return client.post("/api/support", json=BODY if body is None else body, headers=headers or {})


def tickets(db: Session) -> list[SupportTicket]:
    return list(db.scalars(select(SupportTicket).order_by(SupportTicket.created_at)))


class FakeProvider:
    """Stands in for Cloudflare: records each request and answers as told."""

    def __init__(self, monkeypatch, *outcomes):
        self.sent: list[tuple[str, dict, dict]] = []
        self.outcomes = list(outcomes) or [{"success": True, "result": {"delivered": ["x"], "queued": [], "permanent_bounces": []}}]
        monkeypatch.setattr(settings, "email_provider", "cloudflare")
        monkeypatch.setattr(settings, "cloudflare_account_id", "acct123")
        from pydantic import SecretStr

        monkeypatch.setattr(settings, "email_api_token", SecretStr("secret-token-value"))
        monkeypatch.setattr("urllib.request.urlopen", self.urlopen)

    def urlopen(self, request, timeout=None):
        self.sent.append((request.full_url, dict(request.header_items()), json.loads(request.data)))
        outcome = self.outcomes[min(len(self.sent) - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return io.BytesIO(json.dumps(outcome).encode())


# --- the ticket ----------------------------------------------------------------------------------------------------------------


def test_a_message_becomes_a_ticket_with_a_short_random_reference(client: TestClient, db_session: Session):
    response = send(client)

    assert response.status_code == 201, response.text
    reference = response.json()["reference"]
    assert support.is_reference(reference) and reference.startswith("SUP-") and len(reference) == 12
    [ticket] = tickets(db_session)
    assert (ticket.reference, ticket.email, ticket.name, ticket.message, ticket.status) == (reference, "anna@example.test", "Anna Andersson", MESSAGE, "open")
    assert ticket.user_id is None  # nobody was signed in


def test_references_do_not_count_up_and_avoid_look_alike_letters():
    seen = {support.new_reference() for _ in range(500)}
    assert len(seen) == 500  # random, not a sequence
    assert not any(char in reference[4:] for reference in seen for char in "01OIL")
    assert not support.is_reference("SUP-ABCDEFG1") and not support.is_reference("sup-abcdefgh") and not support.is_reference("SUP-ABCDEFGHI")


def test_a_signed_in_sender_is_recognised_by_the_server_and_their_account_wins(client: TestClient, db_session: Session):
    user = make_user(db_session, name="Tina Therapist")

    send(client, {**BODY, "name": "Someone Else", "email": "someone@else.test"}, {"X-Dev-User-Email": user.email})

    [ticket] = tickets(db_session)
    assert ticket.user_id == user.id and ticket.email == user.email and ticket.name == "Tina Therapist"


def test_a_stale_or_unknown_login_is_just_anonymous(client: TestClient, db_session: Session):
    assert send(client, headers={"X-Dev-User-Email": "nobody@nowhere.test"}).status_code == 201
    assert tickets(db_session)[0].user_id is None


# --- what is refused ------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        {"email": "not an address"},
        {"email": "a@b"},
        {"email": "two@@example.test"},
        {"email": 'quote"@example.test'},
        {"email": "x" * 250 + "@example.test"},
        {"message": "short"},
        {"message": "   "},
        {"message": "x" * 4001},
        {"name": "n" * 121},
        {"unexpected": "field"},
    ],
)
def test_bad_input_is_refused_and_nothing_is_kept(client: TestClient, db_session: Session, change):
    assert send(client, {**BODY, **change}).status_code == 422
    assert tickets(db_session) == []


def test_the_longest_allowed_message_is_accepted(client: TestClient, db_session: Session):
    assert send(client, {**BODY, "message": "x" * 4000}).status_code == 201
    assert len(tickets(db_session)[0].message) == 4000


def test_a_program_that_fills_the_hidden_field_looks_successful_and_keeps_nothing(client: TestClient, db_session: Session, monkeypatch):
    provider = FakeProvider(monkeypatch)
    response = send(client, {**BODY, "website": "http://spam.example"})

    assert response.status_code == 201 and support.is_reference(response.json()["reference"])
    assert tickets(db_session) == [] and provider.sent == []  # no ticket and no email


def test_one_address_can_write_only_a_few_times_an_hour(client: TestClient, db_session: Session, monkeypatch):
    monkeypatch.setattr(settings, "support_tickets_per_hour", 2)
    assert [send(client).status_code for _ in range(2)] == [201, 201]
    third = send(client)
    assert third.status_code == 429 and third.headers["retry-after"] == "3600"
    assert send(client, {**BODY, "email": "other@example.test"}).status_code == 429  # the same SOURCE is limited too
    assert len(tickets(db_session)) == 2  # a refused request keeps nothing


def test_one_address_is_limited_even_from_other_sources(client: TestClient, db_session: Session, monkeypatch):
    monkeypatch.setattr(settings, "support_tickets_per_hour", 2)
    for _ in range(2):
        assert send(client).status_code == 201
        rate_limit.reset()  # a different source each time
    assert send(client).status_code == 429


def test_a_flood_hits_the_global_valve(client: TestClient, db_session: Session, monkeypatch):
    monkeypatch.setattr(settings, "support_tickets_global_per_hour", 2)
    monkeypatch.setattr(settings, "support_tickets_per_hour", 100)
    for index in range(2):
        assert send(client, {**BODY, "email": f"p{index}@example.test"}).status_code == 201
    busy = send(client, {**BODY, "email": "late@example.test"})
    assert busy.status_code == 503 and busy.json()["detail"]["code"] == "busy"


# --- the emails -----------------------------------------------------------------------------------------------------------------


def test_with_no_provider_the_ticket_is_kept_and_says_so(client: TestClient, db_session: Session):
    send(client)
    [ticket] = tickets(db_session)
    assert (ticket.email_status, ticket.email_error, ticket.confirmation_status) == ("not_configured", "not_configured", None)


def test_the_message_goes_to_support_and_a_confirmation_to_the_sender(client: TestClient, db_session: Session, monkeypatch):
    provider = FakeProvider(monkeypatch)
    reference = send(client).json()["reference"]

    (url_1, headers_1, to_support), (url_2, _, confirmation) = provider.sent
    assert url_1 == url_2 == "https://api.cloudflare.com/client/v4/accounts/acct123/email/sending/send"
    assert headers_1["Authorization"] == "Bearer secret-token-value" and headers_1["Content-type"] == "application/json"
    assert to_support["to"] == ["support@bygdell.solutions"] and to_support["reply_to"] == "anna@example.test"
    assert to_support["from"] == settings.email_from and f"[{reference}]" in to_support["subject"] and MESSAGE in to_support["text"]
    assert confirmation["to"] == ["anna@example.test"] and reference in confirmation["subject"] and reference in confirmation["text"]
    assert MESSAGE not in confirmation["text"] and "Anna" not in confirmation["text"]  # nothing the sender wrote is echoed to anyone
    [ticket] = tickets(db_session)
    assert (ticket.email_status, ticket.email_error, ticket.confirmation_status) == ("sent", None, "sent")


def test_a_queued_answer_counts_as_accepted(client: TestClient, db_session: Session, monkeypatch):
    FakeProvider(monkeypatch, {"success": True, "result": {"delivered": [], "queued": ["x"], "permanent_bounces": []}})
    send(client)
    assert tickets(db_session)[0].email_status == "queued"


@pytest.mark.parametrize(
    ("outcome", "code"),
    [
        (urllib.error.HTTPError("u", 401, "no", {}, None), "unauthorized"),
        (urllib.error.HTTPError("u", 403, "no", {}, None), "unauthorized"),
        (urllib.error.HTTPError("u", 429, "slow", {}, None), "rate_limited"),
        (urllib.error.HTTPError("u", 400, "bad", {}, None), "rejected"),
        (urllib.error.HTTPError("u", 503, "down", {}, None), "unavailable"),
        (urllib.error.URLError("no route"), "unavailable"),
        (TimeoutError(), "unavailable"),
        ({"success": False, "errors": [{"code": 10001}]}, "rejected"),
        ({"success": True, "result": {"delivered": [], "queued": [], "permanent_bounces": ["x"]}}, "rejected"),
    ],
)
def test_a_failing_provider_never_loses_the_ticket_or_fails_the_request(client: TestClient, db_session: Session, monkeypatch, outcome, code):
    provider = FakeProvider(monkeypatch, outcome)

    response = send(client)

    assert response.status_code == 201  # the sender is told their message arrived: it did
    [ticket] = tickets(db_session)
    assert (ticket.email_status, ticket.email_error, ticket.confirmation_status) == ("failed", code, None)
    assert len(provider.sent) == 1  # no confirmation promising an answer when support was not reached
    assert "secret-token-value" not in response.text


def test_a_line_break_in_a_name_cannot_inject_a_header(client: TestClient, db_session: Session, monkeypatch):
    provider = FakeProvider(monkeypatch)
    send(client, {**BODY, "name": "Anna\r\nBcc: victim@example.test"})
    subject = provider.sent[0][2]["subject"]
    assert "\n" not in subject and "\r" not in subject
    assert "bcc" not in provider.sent[0][2]  # nothing became a real header or recipient


def test_the_token_never_appears_in_a_log_line(client: TestClient, db_session: Session, monkeypatch, caplog):
    FakeProvider(monkeypatch, urllib.error.HTTPError("u", 401, "no", {}, None))
    with caplog.at_level("DEBUG"):
        send(client)
    assert "secret-token-value" not in caplog.text and MESSAGE not in caplog.text


# --- erasing an account, and the operator ----------------------------------------------------------------------------------------


def test_erasing_an_account_takes_the_words_out_of_its_tickets(client: TestClient, db_session: Session):
    user = make_user(db_session, name="Tina Therapist")
    mine_reference = send(client, headers={"X-Dev-User-Email": user.email}).json()["reference"]
    others_reference = send(client, {**BODY, "email": "other@example.test"}).json()["reference"]
    from datetime import datetime, timezone

    from app.core import account_data

    account_data.erase_account(db_session, user, datetime.now(timezone.utc))

    by_reference = {t.reference: t for t in tickets(db_session)}  # both were made in one transaction: the time does not order them
    mine, others = by_reference[mine_reference], by_reference[others_reference]
    assert (mine.email, mine.name, mine.message, mine.user_id) == ("erased", None, "[erased]", None)
    assert (others.email, others.message) == ("other@example.test", MESSAGE)  # nobody else's ticket is touched


def test_the_operator_can_list_read_resend_and_close_a_ticket(client: TestClient, db_session: Session, monkeypatch):
    reference = send(client).json()["reference"]

    line = admin.support_tickets_report(db_session)[0]
    assert line.startswith(reference) and "email=not_configured" in line and "anna@example.test" in line
    assert MESSAGE in admin.support_show(db_session, reference=reference)

    provider = FakeProvider(monkeypatch)  # email is set up later
    assert admin.support_resend(db_session, reference=reference.lower()) == "email to support: sent"  # a typed-in number may be lower case
    assert len(provider.sent) == 2

    admin.support_close(db_session, reference=reference)
    assert admin.support_tickets_report(db_session) == []
    with pytest.raises(admin.OperatorError):
        admin.support_show(db_session, reference="SUP-NOSUCHXX")
