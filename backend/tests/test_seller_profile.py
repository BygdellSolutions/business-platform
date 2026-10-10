"""Slice 10: the seller's contact and payment details, F-tax, payment terms and the document language.

Owners and admins set them in Settings; every member reads them. IBAN and BIC are checked for shape only (spaces are
removed, letters upper-cased); bankgiro and plusgiro are kept as typed. An invoice keeps them in its issuer snapshot
(schema 2), re-taken when it is issued, and its due date follows the payment terms when none is given.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Role
from tests.invoicing_support import completed, draft_invoice, issue, member_of

PROFILE = {
    "phone": "090-12 34 56",
    "email": "info@example.test",
    "website": "https://example.test",
    "bankgiro": "123-4567",
    "plusgiro": "12 34 56-7",
    "iban": "se45 5000 0000 0583 9825 7466",
    "bic": "eslssess",
    "payment_terms_days": 30,
    "approved_for_f_tax": True,
    "document_language": "sv",
}


def test_an_owner_sets_the_profile_and_iban_and_bic_are_normalized(client: TestClient, sales):
    response = client.patch("/api/organization", json=PROFILE, headers=sales.headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["iban"], body["bic"], body["bankgiro"], body["plusgiro"]) == ("SE4550000000058398257466", "ESLSSESS", "123-4567", "12 34 56-7")
    assert (body["payment_terms_days"], body["approved_for_f_tax"], body["document_language"]) == (30, True, "sv")
    cleared = client.patch("/api/organization", json={"iban": "", "approved_for_f_tax": None}, headers=sales.headers).json()
    assert (cleared["iban"], cleared["approved_for_f_tax"]) == (None, None)


@pytest.mark.parametrize(
    "field,value",
    [("iban", "SE45"), ("iban", "12345678901234567"), ("bic", "ESLS"), ("payment_terms_days", 366), ("payment_terms_days", -1), ("document_language", "de")],
)
def test_malformed_values_are_refused(client: TestClient, sales, field, value):
    assert client.patch("/api/organization", json={field: value}, headers=sales.headers).status_code == 422


@pytest.mark.parametrize("role", [Role.ACCOUNTANT, Role.EMPLOYEE, Role.VIEWER])
def test_only_owners_and_admins_change_it_but_every_member_reads_it(client: TestClient, db_session: Session, sales, role):
    client.patch("/api/organization", json=PROFILE, headers=sales.headers)
    headers = member_of(db_session, sales.org, role)

    assert client.patch("/api/organization", json={"bankgiro": "999-9999"}, headers=headers).status_code == 403
    assert client.get("/api/organization", headers=headers).json()["bankgiro"] == "123-4567"


def test_an_invoice_keeps_the_payment_details_and_takes_its_due_date_from_the_terms(client: TestClient, db_session: Session, sales):
    client.patch("/api/organization", json=PROFILE, headers=sales.headers)

    invoice = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing), invoice_date="2026-10-08")
    explicit = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing), invoice_date="2026-10-08", due_date="2026-10-20")

    assert invoice["due_date"] == "2026-11-07" and explicit["due_date"] == "2026-10-20"
    snapshot = invoice["issuer_snapshot"]
    assert snapshot["schema"] == 3 and snapshot["our_reference"]  # who made the draft, until it is issued
    assert {key: snapshot[key] for key in PROFILE} == {**PROFILE, "iban": "SE4550000000058398257466", "bic": "ESLSSESS"}

    # Issuing re-takes the snapshot; changing the settings afterwards never changes the issued invoice.
    client.patch("/api/organization", json={"bankgiro": "765-4321"}, headers=sales.headers)
    issued = issue(client, sales.headers, client.get(f"/api/invoices/{invoice['id']}", headers=sales.headers).json())
    client.patch("/api/organization", json={"bankgiro": "111-1111"}, headers=sales.headers)
    assert issued["issuer_snapshot"]["bankgiro"] == "765-4321"
    assert client.get(f"/api/invoices/{invoice['id']}", headers=sales.headers).json()["issuer_snapshot"]["bankgiro"] == "765-4321"


def test_without_payment_terms_an_invoice_has_no_due_date_unless_given(client: TestClient, db_session: Session, sales):
    invoice = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing), invoice_date=str(date(2026, 10, 8)))
    assert invoice["due_date"] is None


def test_our_reference_is_who_issued_the_invoice_by_name_at_that_moment(client: TestClient, db_session: Session, sales):
    from app.models import User
    from tests.factories import add_member, make_user

    issuer = make_user(db_session, name="Tina Accountant")
    add_member(db_session, sales.org, issuer, Role.ACCOUNTANT)
    headers = {"X-Dev-User-Email": issuer.email}
    draft = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing))

    issued = issue(client, headers, draft)
    db_session.get(User, issuer.id).name = "Tina Renamed"
    db_session.flush()

    assert issued["issuer_snapshot"]["our_reference"] == "Tina Accountant"
    assert client.get(f"/api/invoices/{issued['id']}", headers=headers).json()["issuer_snapshot"]["our_reference"] == "Tina Accountant"
