"""Emailing an issued invoice to a customer: the frozen PDF as an attachment, a sender that names the organization, a log of every
attempt, who may do it, and that another organization can neither send nor read it.
"""

import base64
import hashlib
import io
import json
import urllib.error

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core import email as mail
from app.core import rate_limit
from app.core.config import settings
from app.models import Role
from app.modules.invoicing import emailing
from app.modules.invoicing.models import InvoiceEmail
from tests.invoicing_support import INVOICES, Two, completed, draft_invoice, issue, member_of

GOOD = {"to": "kund@example.test", "note": "Tack för senast!"}


@pytest.fixture(autouse=True)
def fresh_limits():
    rate_limit.reset()
    yield
    rate_limit.reset()


class FakeProvider:
    """Stands in for Cloudflare: records each request and answers as told."""

    def __init__(self, monkeypatch, outcome=None):
        self.sent: list[tuple[str, dict, dict]] = []
        self.outcome = outcome or {"success": True, "result": {"delivered": ["x"], "queued": [], "permanent_bounces": []}}
        monkeypatch.setattr(settings, "email_provider", "cloudflare")
        monkeypatch.setattr(settings, "cloudflare_account_id", "acct123")
        from pydantic import SecretStr

        monkeypatch.setattr(settings, "email_api_token", SecretStr("secret-token-value"))
        monkeypatch.setattr("urllib.request.urlopen", self.urlopen)

    def urlopen(self, request, timeout=None):
        self.sent.append((request.full_url, dict(request.header_items()), json.loads(request.data)))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return io.BytesIO(json.dumps(self.outcome).encode())


def issued(db: Session, client: TestClient, headers, org, *, customer=None):
    invoice = issue(client, headers, draft_invoice(client, headers, completed(db, org, customer)))
    return invoice


def pdf_of(client: TestClient, headers, invoice) -> bytes:
    return client.get(f"{INVOICES}/{invoice['id']}/pdf", headers=headers).content


def send(client: TestClient, headers, invoice, body=None):
    return client.post(f"{INVOICES}/{invoice['id']}/email", json=GOOD if body is None else body, headers=headers)


# --- what is sent ----------------------------------------------------------------------------------------------------------------


def test_the_frozen_pdf_goes_as_an_attachment_from_the_organization_with_the_facts_in_the_text(client: TestClient, db_session: Session, monkeypatch):
    two = Two(db_session)
    invoice = issued(db_session, client, two.a, two.a_org, customer=two.a_customer)
    provider = FakeProvider(monkeypatch)

    response = send(client, two.a, invoice)

    assert response.status_code == 201, response.text
    (url, headers, body) = provider.sent[0]
    assert url == "https://api.cloudflare.com/client/v4/accounts/acct123/email/sending/send"
    assert headers["Authorization"] == "Bearer secret-token-value"
    assert body["from"] == {"address": "org-a@invoices.bygdell.solutions", "name": "Org A"}  # the receiver sees whom it is from
    assert body["to"] == ["kund@example.test"]
    assert body["subject"] in (f"Faktura {invoice['number_text']}", f"Invoice {invoice['number_text']}")  # no "from Org A": the sender name says it
    assert "Org A" not in body["subject"]
    [attachment] = body["attachments"]
    assert attachment["type"] == "application/pdf" and attachment["disposition"] == "attachment" and attachment["filename"].endswith(".pdf")
    assert base64.b64decode(attachment["content"]) == pdf_of(client, two.a, invoice)  # the very bytes a download gives
    text_body = body["text"]
    assert invoice["number_text"] in text_body and "Tack för senast!" in text_body and invoice["currency"] in text_body
    assert "Org A" in text_body


def test_replies_go_to_the_organization_else_to_the_person_who_sent_it(client: TestClient, db_session: Session, monkeypatch):
    two = Two(db_session)
    two.a_org.email = "kontor@org-a.test"  # frozen on the invoice when it is issued
    db_session.flush()
    with_email = issued(db_session, client, two.a, two.a_org, customer=two.a_customer)
    without_email = issued(db_session, client, two.b, two.b_org, customer=two.b_customer)
    provider = FakeProvider(monkeypatch)

    send(client, two.a, with_email)
    send(client, two.b, without_email)

    assert provider.sent[0][2]["reply_to"] == "kontor@org-a.test"
    assert provider.sent[1][2]["reply_to"].endswith("@tests.invalid")  # no organization email: the person who pressed send


def test_the_sender_name_is_a_plain_address_part():
    assert emailing.sender_local_part("Umeå Häst & Rehab") == "umea-hast-rehab"
    assert emailing.sender_local_part("   ") == "invoices" and emailing.sender_local_part("日本") == "invoices"
    assert len(emailing.sender_local_part("a" * 200)) <= 40 and not emailing.sender_local_part("-x-").startswith("-")


def test_amounts_are_written_the_way_each_language_writes_them():
    from decimal import Decimal

    assert emailing._money(Decimal("1250.5"), "SEK", "sv") == "1 250,50 SEK"
    assert emailing._money(Decimal("1234567.00"), "EUR", "en") == "1 234 567.00 EUR"
    assert emailing._money(Decimal("0"), "SEK", "sv") == "0,00 SEK"


# --- the log, the history and failures ---------------------------------------------------------------------------------------------


def test_every_attempt_is_logged_with_who_and_the_hash_of_what_was_attached(client: TestClient, db_session: Session, monkeypatch):
    two = Two(db_session)
    invoice = issued(db_session, client, two.a, two.a_org, customer=two.a_customer)
    FakeProvider(monkeypatch)
    send(client, two.a, invoice)

    [row] = db_session.scalars(select(InvoiceEmail)).all()
    assert (row.to_email, row.status, row.error, row.note) == ("kund@example.test", "sent", None, "Tack för senast!")
    assert row.pdf_sha256 == hashlib.sha256(pdf_of(client, two.a, invoice)).hexdigest() and row.created_by is not None
    listed = client.get(f"{INVOICES}/{invoice['id']}/emails", headers=two.a).json()
    assert [(e["to_email"], e["status"]) for e in listed] == [("kund@example.test", "sent")] and listed[0]["sent_by_name"]
    history = client.get("/api/history", params={"entity_type": "invoice", "entity_id": invoice["id"]}, headers=two.a).json()["events"]
    assert any(e["action"] == "emailed" and e["changes"]["to"]["to"] == "kund@example.test" for e in history)
    assert "JVBER" not in str(history)  # the PDF itself is not in the history


@pytest.mark.parametrize(
    ("outcome", "code"),
    [
        (urllib.error.HTTPError("u", 401, "no", {}, None), "unauthorized"),
        (urllib.error.URLError("no route"), "unavailable"),
        ({"success": True, "result": {"delivered": [], "queued": [], "permanent_bounces": ["x"]}}, "rejected"),
    ],
)
def test_a_failed_send_is_kept_and_reported_never_hidden(client: TestClient, db_session: Session, monkeypatch, outcome, code):
    two = Two(db_session)
    invoice = issued(db_session, client, two.a, two.a_org, customer=two.a_customer)
    FakeProvider(monkeypatch, outcome)

    response = send(client, two.a, invoice)

    assert response.status_code == 201 and response.json()["status"] == "failed" and response.json()["error"] == code
    assert "secret-token-value" not in response.text
    history = client.get("/api/history", params={"entity_type": "invoice", "entity_id": invoice["id"]}, headers=two.a).json()["events"]
    assert any(e["action"] == "email_failed" for e in history)


def test_with_no_provider_the_person_is_told_it_was_not_sent(client: TestClient, db_session: Session):
    two = Two(db_session)
    invoice = issued(db_session, client, two.a, two.a_org, customer=two.a_customer)
    response = send(client, two.a, invoice)
    assert response.status_code == 201 and (response.json()["status"], response.json()["error"]) == ("failed", "not_configured")


def test_a_queued_answer_counts_as_accepted(client: TestClient, db_session: Session, monkeypatch):
    two = Two(db_session)
    invoice = issued(db_session, client, two.a, two.a_org, customer=two.a_customer)
    FakeProvider(monkeypatch, {"success": True, "result": {"delivered": [], "queued": ["x"], "permanent_bounces": []}})
    assert send(client, two.a, invoice).json()["status"] == "queued"


# --- what is refused --------------------------------------------------------------------------------------------------------------


def test_a_draft_cannot_be_emailed(client: TestClient, db_session: Session, monkeypatch):
    two = Two(db_session)
    provider = FakeProvider(monkeypatch)
    draft = draft_invoice(client, two.a, completed(db_session, two.a_org, two.a_customer))
    response = send(client, two.a, draft)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "invoice_not_issued"
    assert provider.sent == [] and db_session.scalars(select(InvoiceEmail)).all() == []


@pytest.mark.parametrize("address", ["", "not an address", "a@b", "two@@example.test", 'q"@example.test', "x" * 250 + "@example.test"])
def test_a_bad_address_is_refused_before_anything_is_sent(client: TestClient, db_session: Session, monkeypatch, address):
    two = Two(db_session)
    invoice = issued(db_session, client, two.a, two.a_org, customer=two.a_customer)
    provider = FakeProvider(monkeypatch)
    assert send(client, two.a, invoice, {"to": address}).status_code == 422
    assert provider.sent == []


def test_unknown_fields_and_a_huge_note_are_refused(client: TestClient, db_session: Session, monkeypatch):
    two = Two(db_session)
    invoice = issued(db_session, client, two.a, two.a_org, customer=two.a_customer)
    FakeProvider(monkeypatch)
    assert send(client, two.a, invoice, {**GOOD, "from": "ceo@evil.test"}).status_code == 422  # the sender is never the caller's choice
    assert send(client, two.a, invoice, {**GOOD, "cc": "x@example.test"}).status_code == 422
    assert send(client, two.a, invoice, {**GOOD, "note": "x" * 2001}).status_code == 422


@pytest.mark.parametrize(("role", "allowed"), [(Role.OWNER, True), (Role.ADMIN, True), (Role.ACCOUNTANT, True), (Role.EMPLOYEE, False), (Role.VIEWER, False)])
def test_only_the_people_who_do_the_books_may_send(client: TestClient, db_session: Session, monkeypatch, role, allowed):
    two = Two(db_session)
    invoice = issued(db_session, client, two.a, two.a_org, customer=two.a_customer)
    FakeProvider(monkeypatch)
    headers = member_of(db_session, two.a_org, role)
    assert send(client, headers, invoice).status_code == (201 if allowed else 403)
    assert client.get(f"{INVOICES}/{invoice['id']}/emails", headers=headers).status_code == 200  # anyone who can read the invoice sees the log


def test_another_organizations_invoice_can_neither_be_sent_nor_read(client: TestClient, db_session: Session, monkeypatch):
    two = Two(db_session)
    mine = issued(db_session, client, two.a, two.a_org, customer=two.a_customer)
    theirs = issued(db_session, client, two.b, two.b_org, customer=two.b_customer)
    provider = FakeProvider(monkeypatch)
    send(client, two.b, theirs)

    assert send(client, two.a, theirs).status_code == 404 and send(client, two.b, mine).status_code == 404
    assert client.get(f"{INVOICES}/{theirs['id']}/emails", headers=two.a).status_code == 404
    assert len(provider.sent) == 1  # only B's own send went out
    assert [e["to_email"] for e in client.get(f"{INVOICES}/{mine['id']}/emails", headers=two.a).json()] == []


def test_the_database_refuses_a_log_row_for_another_organizations_invoice(client: TestClient, db_session: Session):
    two = Two(db_session)
    theirs = issued(db_session, client, two.b, two.b_org, customer=two.b_customer)
    with pytest.raises(Exception, match="fk_invoice_emails_invoice_same_organization"):
        db_session.execute(
            text(
                "INSERT INTO invoice_emails (organization_id, invoice_id, to_email, sender_address, subject, pdf_sha256, status) "
                "VALUES (:org, :invoice, 'x@example.test', 'a@b.test', 's', :hash, 'sent')"
            ),
            {"org": two.a_org.id, "invoice": theirs["id"], "hash": "0" * 64},
        )


def test_an_organization_has_a_daily_limit_that_failures_count_against(client: TestClient, db_session: Session, monkeypatch):
    two = Two(db_session)
    invoice = issued(db_session, client, two.a, two.a_org, customer=two.a_customer)
    FakeProvider(monkeypatch, urllib.error.HTTPError("u", 401, "no", {}, None))
    monkeypatch.setattr(settings, "invoice_emails_per_day", 2)

    assert [send(client, two.a, invoice).status_code for _ in range(2)] == [201, 201]
    third = send(client, two.a, invoice)
    assert third.status_code == 429 and third.json()["detail"]["code"] == "rate_limited"
    other = issued(db_session, client, two.b, two.b_org, customer=two.b_customer)
    assert send(client, two.b, other).status_code == 201  # another organization has its own allowance
    assert mail.looks_like_address("kund@example.test")


def test_a_line_break_in_the_note_cannot_inject_a_header(client: TestClient, db_session: Session, monkeypatch):
    two = Two(db_session)
    invoice = issued(db_session, client, two.a, two.a_org, customer=two.a_customer)
    provider = FakeProvider(monkeypatch)
    send(client, two.a, invoice, {"to": "kund@example.test", "note": "Hej\r\nBcc: victim@example.test"})
    body = provider.sent[0][2]
    assert "bcc" not in body and "\n" not in body["subject"]  # the note is text in the body, never a header


# --- "sent" on the invoice itself: shown on the list and the invoice, only for an email that really went ---------------------------


def _list(client: TestClient, headers) -> dict:
    return {row["id"]: row for row in client.get(INVOICES, headers=headers).json()}


def test_an_invoice_says_it_was_emailed_only_after_an_email_really_went(client: TestClient, db_session: Session, monkeypatch):
    two = Two(db_session)
    invoice = issued(db_session, client, two.a, two.a_org, customer=two.a_customer)
    assert _list(client, two.a)[invoice["id"]]["emailed"] is False
    assert client.get(f"{INVOICES}/{invoice['id']}", headers=two.a).json()["emailed"] is False

    send(client, two.a, invoice)  # no provider: the attempt failed, so the customer got nothing
    assert _list(client, two.a)[invoice["id"]]["emailed"] is False

    FakeProvider(monkeypatch)
    send(client, two.a, invoice)
    assert _list(client, two.a)[invoice["id"]]["emailed"] is True
    assert client.get(f"{INVOICES}/{invoice['id']}", headers=two.a).json()["emailed"] is True


def test_a_queued_email_counts_as_sent_and_a_draft_never_does(client: TestClient, db_session: Session, monkeypatch):
    two = Two(db_session)
    invoice = issued(db_session, client, two.a, two.a_org, customer=two.a_customer)
    FakeProvider(monkeypatch, {"success": True, "result": {"delivered": [], "queued": ["x"], "permanent_bounces": []}})
    send(client, two.a, invoice)
    draft = draft_invoice(client, two.a, completed(db_session, two.a_org, two.a_customer))
    rows = _list(client, two.a)
    assert rows[invoice["id"]]["emailed"] is True and rows[draft["id"]]["emailed"] is False


def test_another_organizations_email_never_marks_my_invoice(client: TestClient, db_session: Session, monkeypatch):
    two = Two(db_session)
    mine = issued(db_session, client, two.a, two.a_org, customer=two.a_customer)
    theirs = issued(db_session, client, two.b, two.b_org, customer=two.b_customer)
    FakeProvider(monkeypatch)
    send(client, two.b, theirs)
    assert _list(client, two.a)[mine["id"]]["emailed"] is False
    assert _list(client, two.b)[theirs["id"]]["emailed"] is True
