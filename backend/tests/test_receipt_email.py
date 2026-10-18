"""Emailing the receipt of an order paid at the counter: the receipt PDF (with the organization's current logo) as an attachment, a sender that
names the organization, a log of every attempt, who may do it, and that another organization can neither send nor read it.
"""

import base64
import io
import urllib.error

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import rate_limit
from app.core.config import settings
from app.models import Role
from app.modules.invoicing.models import ReceiptEmail
from tests.factories import make_customer, make_org
from tests.invoicing_support import member_of
from tests.pdf_support import pdf_text, reader
from tests.test_counter_sales import _draft, _pay
from tests.test_invoice_email import FakeProvider

RECEIPTS = "/api/invoices/receipts"
GOOD = {"to": "kund@example.test", "note": "Välkommen åter!"}


@pytest.fixture(autouse=True)
def fresh_limits():
    rate_limit.reset()
    yield
    rate_limit.reset()


@pytest.fixture
def world(db_session: Session):
    org = make_org(db_session, "Kalle Hästterapi")
    return org, member_of(db_session, org, Role.OWNER), make_customer(db_session, org, "Anna Andersson", email="anna@example.test")


def paid_order(client: TestClient, owner, customer, method="swish") -> dict:
    return _pay(client, owner, _draft(client, owner, customer.id), method).json()


def send(client: TestClient, headers, order: dict, body=None):
    return client.post(f"{RECEIPTS}/{order['id']}/email", json=GOOD if body is None else body, headers=headers)


def logo(color) -> str:
    out = io.BytesIO()
    Image.new("RGB", (300, 100), color).save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode()


# --- what is sent --------------------------------------------------------------------------------------------------------------------


def test_the_receipt_with_the_current_logo_goes_as_an_attachment_from_the_organization(client: TestClient, db_session: Session, world, monkeypatch):
    org, owner, anna = world
    assert client.post("/api/organization/logo", json={"data_base64": logo((200, 0, 0))}, headers=owner).status_code == 200
    order = paid_order(client, owner, anna, "card")
    provider = FakeProvider(monkeypatch)

    response = send(client, owner, order)

    assert response.status_code == 201, response.text
    (_, headers, body) = provider.sent[0]
    assert headers["Authorization"] == "Bearer secret-token-value"
    assert body["from"] == {"address": "kalle-hastterapi@invoices.bygdell.solutions", "name": "Kalle Hästterapi"}
    assert body["to"] == ["kund@example.test"] and body["subject"] in ("Kvitto 1001", "Receipt 1001")
    [attachment] = body["attachments"]
    assert attachment["type"] == "application/pdf" and attachment["filename"] == "receipt-1001.pdf"
    pdf = base64.b64decode(attachment["content"])
    page = reader(pdf).pages[0]
    assert len(page.images) == 1 and page.images[0].image.convert("RGB").getpixel((1, 1)) == (200, 0, 0)  # the logo is on the receipt that was sent
    assert "Kalle Hästterapi" in pdf_text(pdf) and "1001" in pdf_text(pdf)
    text = body["text"]
    assert "Välkommen åter!" in text and "1001" in text and "1 000,00 SEK" in text  # the organization writes Swedish unless it says English
    assert "Betalsätt: kort" in text  # how it was paid


def test_the_text_is_in_the_documents_language(client: TestClient, db_session: Session, world, monkeypatch):
    org, owner, anna = world
    order = paid_order(client, owner, anna)
    provider = FakeProvider(monkeypatch)
    client.patch("/api/organization", json={"document_language": "en"}, headers=owner)
    send(client, owner, order)
    english = provider.sent[0][2]
    assert english["subject"] == "Receipt 1001" and "Thank you for your purchase" in english["text"] and "Paid by: Swish" in english["text"]
    client.patch("/api/organization", json={"document_language": "sv"}, headers=owner)
    send(client, owner, order)
    swedish = provider.sent[1][2]
    assert swedish["subject"] == "Kvitto 1001" and "Tack för ditt köp" in swedish["text"] and "Betalsätt: Swish" in swedish["text"]


def test_replies_go_to_the_organization_else_to_the_person_who_sent_it(client: TestClient, db_session: Session, world, monkeypatch):
    org, owner, anna = world
    order = paid_order(client, owner, anna)
    provider = FakeProvider(monkeypatch)
    send(client, owner, order)
    assert provider.sent[0][2]["reply_to"].endswith("@tests.invalid")  # no organization email yet
    client.patch("/api/organization", json={"email": "kontor@kalle.example"}, headers=owner)
    send(client, owner, order)
    assert provider.sent[1][2]["reply_to"] == "kontor@kalle.example"


def test_the_draft_has_the_customers_address_and_none_for_a_walk_in(client: TestClient, db_session: Session, world):
    org, owner, anna = world
    named = paid_order(client, owner, anna)
    draft = client.get(f"{RECEIPTS}/{named['id']}/email-draft", headers=owner).json()
    assert draft == {"to": "anna@example.test", "subject": draft["subject"], "message": ""} and draft["subject"] in ("Kvitto 1001", "Receipt 1001")

    walk_in = client.post("/api/customers/walk-in", headers=owner).json()
    counter = _pay(client, owner, _draft(client, owner, walk_in["id"])).json()
    assert client.get(f"{RECEIPTS}/{counter['id']}/email-draft", headers=owner).json()["to"] == ""


# --- the log, the history and failures -------------------------------------------------------------------------------------------------


def test_every_attempt_is_logged_with_who_and_the_hash_of_what_was_attached(client: TestClient, db_session: Session, world, monkeypatch):
    org, owner, anna = world
    order = paid_order(client, owner, anna)
    FakeProvider(monkeypatch)
    send(client, owner, order)

    [row] = db_session.scalars(select(ReceiptEmail)).all()
    assert (row.to_email, row.status, row.error, row.note) == ("kund@example.test", "sent", None, "Välkommen åter!")
    assert len(row.pdf_sha256) == 64 and row.created_by is not None
    listed = client.get(f"{RECEIPTS}/{order['id']}/emails", headers=owner).json()
    assert [(e["to_email"], e["status"]) for e in listed] == [("kund@example.test", "sent")]
    history = client.get("/api/history", params={"entity_type": "transaction", "entity_id": order["id"]}, headers=owner).json()["events"]
    assert any(e["action"] == "receipt_emailed" and e["changes"]["to"]["to"] == "kund@example.test" for e in history)


@pytest.mark.parametrize(
    ("outcome", "code"),
    [(urllib.error.HTTPError("u", 401, "no", {}, None), "unauthorized"), (urllib.error.URLError("no route"), "unavailable")],
)
def test_a_failed_send_is_kept_and_reported(client: TestClient, db_session: Session, world, monkeypatch, outcome, code):
    org, owner, anna = world
    order = paid_order(client, owner, anna)
    FakeProvider(monkeypatch, outcome)
    response = send(client, owner, order)
    assert response.status_code == 201 and (response.json()["status"], response.json()["error"]) == ("failed", code)
    assert "secret-token-value" not in response.text


def test_with_no_provider_the_person_is_told_it_was_not_sent(client: TestClient, db_session: Session, world):
    org, owner, anna = world
    response = send(client, owner, paid_order(client, owner, anna))
    assert (response.json()["status"], response.json()["error"]) == ("failed", "not_configured")


# --- what is refused ---------------------------------------------------------------------------------------------------------------------


def test_an_order_that_was_not_paid_at_the_counter_has_no_receipt_to_send(client: TestClient, db_session: Session, world, monkeypatch):
    org, owner, anna = world
    provider = FakeProvider(monkeypatch)
    unpaid = _draft(client, owner, anna.id)
    for call in (lambda: send(client, owner, unpaid), lambda: client.get(f"{RECEIPTS}/{unpaid['id']}/email-draft", headers=owner)):
        response = call()
        assert response.status_code == 409 and response.json()["detail"]["code"] == "not_paid_at_counter"
    assert provider.sent == [] and db_session.scalars(select(ReceiptEmail)).all() == []


@pytest.mark.parametrize("address", ["", "not an address", "a@b", "two@@example.test", 'q"@example.test'])
def test_a_bad_address_is_refused_before_anything_is_sent(client: TestClient, db_session: Session, world, monkeypatch, address):
    org, owner, anna = world
    order = paid_order(client, owner, anna)
    provider = FakeProvider(monkeypatch)
    assert send(client, owner, order, {"to": address}).status_code == 422
    assert provider.sent == []


def test_the_sender_is_never_the_callers_choice(client: TestClient, db_session: Session, world, monkeypatch):
    org, owner, anna = world
    order = paid_order(client, owner, anna)
    FakeProvider(monkeypatch)
    assert send(client, owner, order, {**GOOD, "from": "ceo@evil.test"}).status_code == 422
    assert send(client, owner, order, {**GOOD, "cc": "x@example.test"}).status_code == 422


@pytest.mark.parametrize(("role", "allowed"), [(Role.OWNER, True), (Role.ADMIN, True), (Role.ACCOUNTANT, True), (Role.EMPLOYEE, True), (Role.VIEWER, False)])
def test_whoever_may_change_records_may_send_a_receipt_a_viewer_may_not(client: TestClient, db_session: Session, world, monkeypatch, role, allowed):
    org, owner, anna = world
    order = paid_order(client, owner, anna)
    FakeProvider(monkeypatch)
    headers = member_of(db_session, org, role)
    assert send(client, headers, order).status_code == (201 if allowed else 403)
    assert client.get(f"{RECEIPTS}/{order['id']}/emails", headers=headers).status_code == 200


def test_another_organizations_receipt_can_neither_be_sent_nor_read(client: TestClient, db_session: Session, world, monkeypatch):
    org, owner, anna = world
    other_org = make_org(db_session, "Other")
    other = member_of(db_session, other_org, Role.OWNER)
    theirs = paid_order(client, other, make_customer(db_session, other_org, "Bo"))
    provider = FakeProvider(monkeypatch)
    assert send(client, owner, theirs).status_code == 404
    assert client.get(f"{RECEIPTS}/{theirs['id']}/emails", headers=owner).status_code == 404
    assert client.get(f"{RECEIPTS}/{theirs['id']}/email-draft", headers=owner).status_code == 404
    assert provider.sent == []


def test_invoices_and_receipts_share_one_daily_allowance_that_failures_count_against(client: TestClient, db_session: Session, world, monkeypatch):
    org, owner, anna = world
    order = paid_order(client, owner, anna)
    FakeProvider(monkeypatch, urllib.error.HTTPError("u", 401, "no", {}, None))
    monkeypatch.setattr(settings, "invoice_emails_per_day", 2)
    assert [send(client, owner, order).status_code for _ in range(2)] == [201, 201]
    third = send(client, owner, order)
    assert third.status_code == 429 and third.json()["detail"]["code"] == "rate_limited"


def test_a_line_break_in_the_subject_cannot_become_a_header(client: TestClient, db_session: Session, world, monkeypatch):
    org, owner, anna = world
    order = paid_order(client, owner, anna)
    provider = FakeProvider(monkeypatch)
    send(client, owner, order, {"to": "kund@example.test", "subject": "Hej\r\nBcc: victim@example.test"})
    body = provider.sent[0][2]
    assert "\n" not in body["subject"] and "bcc" not in body
