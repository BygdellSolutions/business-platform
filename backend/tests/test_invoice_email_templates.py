"""Pre-made invoice email text: the subject and message an organization writes once in Settings, with placeholders that the send form fills
in from the invoice. Only the known placeholders are accepted when saving; the form shows (and the person may change) exactly what is sent.
"""

import io
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core import message_templates, rate_limit
from app.core.config import settings
from app.models import Role
from tests.invoicing_support import INVOICES, Two, completed, draft_invoice, issue, member_of

ORG = "/api/organization"


@pytest.fixture(autouse=True)
def fresh_limits():
    rate_limit.reset()
    yield
    rate_limit.reset()


class Capture:
    def __init__(self, monkeypatch):
        from pydantic import SecretStr

        self.bodies: list[dict] = []
        monkeypatch.setattr(settings, "email_provider", "cloudflare")
        monkeypatch.setattr(settings, "cloudflare_account_id", "acct123")
        monkeypatch.setattr(settings, "email_api_token", SecretStr("secret-token-value"))
        monkeypatch.setattr("urllib.request.urlopen", self.urlopen)

    def urlopen(self, request, timeout=None):
        self.bodies.append(json.loads(request.data))
        return io.BytesIO(json.dumps({"success": True, "result": {"delivered": ["x"], "queued": [], "permanent_bounces": []}}).encode())


def issued(db: Session, client: TestClient, two: Two):
    return issue(client, two.a, draft_invoice(client, two.a, completed(db, two.a_org, two.a_customer)))


# --- the template engine -----------------------------------------------------------------------------------------------------------


def test_known_placeholders_are_filled_in_and_nothing_else_is_touched():
    values = {"customer": "Anna", "invoice_number": "1001", "amount": "850,00 SEK"}
    assert message_templates.render("Hej {customer}, faktura {invoice_number} på {amount}.", values) == "Hej Anna, faktura 1001 på 850,00 SEK."
    assert message_templates.render("Förfaller {due_date}.", values) == "Förfaller ."  # a known placeholder without a value is empty
    assert message_templates.render("Hej\n\nMed vänlig hälsning", values) == "Hej\n\nMed vänlig hälsning"


@pytest.mark.parametrize("text", ["{custmer}", "Hej {Customer}", "{}", "{ customer }", "Hej {customer", "Hej customer}", "{{customer}}"])
def test_anything_else_in_curly_brackets_is_refused(text):
    assert message_templates.problem(text) is not None


@pytest.mark.parametrize("text", ["", "Plain text", "Hej {customer}!", "{customer}{amount}", "{organization}"])
def test_plain_text_and_known_placeholders_are_fine(text):
    assert message_templates.problem(text) is None


def test_a_placeholder_value_that_looks_like_a_placeholder_is_not_filled_in_again():
    assert message_templates.render("{customer}", {"customer": "{amount}", "amount": "999"}) == "{amount}"  # one pass: no second evaluation


# --- saving in Settings ----------------------------------------------------------------------------------------------------------


def test_the_owner_saves_and_reads_back_the_templates(client: TestClient, db_session: Session):
    two = Two(db_session)
    body = {"invoice_email_subject": "Faktura {invoice_number} från {organization}", "invoice_email_message": "Hej {customer},\n\nTack för att du anlitar oss!"}
    saved = client.patch(ORG, json=body, headers=two.a)
    assert saved.status_code == 200, saved.text
    read = client.get(ORG, headers=two.a).json()
    assert (read["invoice_email_subject"], read["invoice_email_message"]) == (body["invoice_email_subject"], body["invoice_email_message"])
    cleared = client.patch(ORG, json={"invoice_email_subject": "  ", "invoice_email_message": None}, headers=two.a).json()
    assert cleared["invoice_email_subject"] is None and cleared["invoice_email_message"] is None  # blank means the built-in text again


@pytest.mark.parametrize(
    "body",
    [
        {"invoice_email_subject": "Hej {custmer}"},
        {"invoice_email_message": "Belopp {price}"},
        {"invoice_email_subject": "two\nlines"},
        {"invoice_email_subject": "x" * 201},
        {"invoice_email_message": "x" * 2001},
    ],
)
def test_a_bad_template_is_refused_and_nothing_changes(client: TestClient, db_session: Session, body):
    two = Two(db_session)
    assert client.patch(ORG, json=body, headers=two.a).status_code == 422
    read = client.get(ORG, headers=two.a).json()
    assert read["invoice_email_subject"] is None and read["invoice_email_message"] is None


def test_the_refusal_names_the_unknown_word_and_lists_the_known_ones(client: TestClient, db_session: Session):
    two = Two(db_session)
    detail = client.patch(ORG, json={"invoice_email_subject": "Hej {custmer}"}, headers=two.a).text
    assert "{custmer}" in detail and "{invoice_number}" in detail


@pytest.mark.parametrize(("role", "allowed"), [(Role.OWNER, True), (Role.ADMIN, True), (Role.ACCOUNTANT, False), (Role.EMPLOYEE, False), (Role.VIEWER, False)])
def test_only_owners_and_admins_change_the_templates(client: TestClient, db_session: Session, role, allowed):
    two = Two(db_session)
    headers = member_of(db_session, two.a_org, role)
    status = client.patch(ORG, json={"invoice_email_message": "Hej {customer}"}, headers=headers).status_code
    assert status == (200 if allowed else 403)


def test_one_organizations_templates_never_reach_another(client: TestClient, db_session: Session):
    two = Two(db_session)
    client.patch(ORG, json={"invoice_email_message": "Secret for A only"}, headers=two.a)
    assert client.get(ORG, headers=two.b).json()["invoice_email_message"] is None


# --- the form's starting point and the send ----------------------------------------------------------------------------------------


def test_the_draft_fills_in_the_invoice_facts_and_the_customer_address(client: TestClient, db_session: Session):
    two = Two(db_session)
    invoice = issued(db_session, client, two)
    template = {
        "invoice_email_subject": "Faktura {invoice_number} ({amount})",
        "invoice_email_message": "Hej {customer}! Förfaller {due_date}. Ref {payment_reference}. /{organization}",
    }
    client.patch(ORG, json=template, headers=two.a)

    draft = client.get(f"{INVOICES}/{invoice['id']}/email-draft", headers=two.a).json()

    assert draft["subject"].startswith(f"Faktura {invoice['number_text']} (") and invoice["currency"] in draft["subject"]
    assert draft["message"].startswith("Hej Anna Andersson! Förfaller ") and draft["message"].endswith("/Org A")
    assert "{" not in draft["subject"] + draft["message"]
    assert "Förfaller -." in draft["message"] or "Förfaller 20" in draft["message"]  # an invoice without a due date says "-", not nothing
    assert draft["to"] == (two.a_customer.email or "")


def test_without_templates_the_draft_has_the_built_in_subject_and_no_message(client: TestClient, db_session: Session):
    two = Two(db_session)
    invoice = issued(db_session, client, two)
    draft = client.get(f"{INVOICES}/{invoice['id']}/email-draft", headers=two.a).json()
    assert draft["subject"] in (f"Faktura {invoice['number_text']}", f"Invoice {invoice['number_text']}") and draft["message"] == ""
    assert "Org A" not in draft["subject"]


def test_the_organization_name_in_a_draft_is_the_one_frozen_on_the_invoice(client: TestClient, db_session: Session):
    two = Two(db_session)
    invoice = issued(db_session, client, two)
    client.patch(ORG, json={"name": "Renamed Org", "invoice_email_message": "From {organization}"}, headers=two.a)
    draft = client.get(f"{INVOICES}/{invoice['id']}/email-draft", headers=two.a).json()
    assert draft["message"] == "From Org A"  # the name on the invoice, not today's


def test_the_draft_of_a_draft_invoice_or_of_another_organizations_invoice_is_refused(client: TestClient, db_session: Session):
    two = Two(db_session)
    a_draft = draft_invoice(client, two.a, completed(db_session, two.a_org, two.a_customer))
    assert client.get(f"{INVOICES}/{a_draft['id']}/email-draft", headers=two.a).status_code == 409
    theirs = issue(client, two.b, draft_invoice(client, two.b, completed(db_session, two.b_org, two.b_customer)))
    assert client.get(f"{INVOICES}/{theirs['id']}/email-draft", headers=two.a).status_code == 404


def test_the_subject_and_message_the_person_saw_are_what_is_sent(client: TestClient, db_session: Session, monkeypatch):
    two = Two(db_session)
    invoice = issued(db_session, client, two)
    capture = Capture(monkeypatch)

    response = client.post(
        f"{INVOICES}/{invoice['id']}/email",
        json={"to": "kund@example.test", "subject": "Min egen rubrik", "note": "Mitt eget meddelande " + "x" * 1500},
        headers=two.a,
    )

    assert response.status_code == 201, response.text
    [body] = capture.bodies
    assert body["subject"] == "Min egen rubrik" and "Mitt eget meddelande" in body["text"]
    assert response.json()["subject"] == "Min egen rubrik"


def test_a_line_break_in_the_subject_cannot_become_a_header(client: TestClient, db_session: Session, monkeypatch):
    two = Two(db_session)
    invoice = issued(db_session, client, two)
    capture = Capture(monkeypatch)
    client.post(f"{INVOICES}/{invoice['id']}/email", json={"to": "kund@example.test", "subject": "Hej\r\nBcc: victim@example.test"}, headers=two.a)
    assert "\n" not in capture.bodies[0]["subject"] and "bcc" not in capture.bodies[0]
