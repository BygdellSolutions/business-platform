"""Payments recorded by hand on issued invoices.

Paid is the sum of an invoice's payments; it never exceeds the gross amount. A payment recorded by mistake is
reversed by a new row (nothing is changed or deleted). Owners, admins and accountants record payments; every member
reads them; another organization's invoice is a 404.
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.org_time import today_in
from app.models import Role
from tests.invoicing_support import Two, completed, draft_invoice, issue, member_of


def _issued(client: TestClient, db_session: Session, sales, **extra):
    """An issued invoice of one completed transaction (850.00 + 25 % VAT = 1062.50)."""
    return issue(client, sales.headers, draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing), **extra))


def _pay(client: TestClient, headers, invoice, amount: str, **extra):
    body = {"amount": amount, "paid_on": str(today_in(None)), "method": "bankgiro", **extra}
    return client.post(f"/api/invoices/{invoice['id']}/payments", json=body, headers=headers)


def test_an_issued_invoice_is_unpaid_then_partially_paid_then_paid(client: TestClient, db_session: Session, sales):
    invoice = _issued(client, db_session, sales)
    assert (invoice["payment_status"], invoice["paid_amount"], invoice["outstanding_amount"]) == ("unpaid", "0.00", "1062.50")

    part = _pay(client, sales.headers, invoice, "500.00", reference="OCR 123")
    rest = _pay(client, sales.headers, invoice, "562.50", method="swish")

    assert part.status_code == 201 and part.json()["payment_status"] == "partially_paid" and part.json()["outstanding_amount"] == "562.50"
    assert rest.json()["payment_status"] == "paid" and rest.json()["outstanding_amount"] == "0.00"
    assert [(p["amount"], p["method"], p["reference"]) for p in rest.json()["payments"]] == [("500.00", "bankgiro", "OCR 123"), ("562.50", "swish", None)]
    assert all(p["created_by_name"] for p in rest.json()["payments"])


def test_a_payment_never_exceeds_what_is_outstanding_and_is_never_dated_in_the_future(client: TestClient, db_session: Session, sales):
    invoice = _issued(client, db_session, sales)
    _pay(client, sales.headers, invoice, "1000.00")

    too_much = _pay(client, sales.headers, invoice, "62.51")
    future = _pay(client, sales.headers, invoice, "1.00", paid_on=str(today_in(None) + timedelta(days=1)))
    zero = _pay(client, sales.headers, invoice, "0")

    assert too_much.status_code == 422 and too_much.json()["detail"][0]["type"] == "payment.overpaid"
    assert "62.50 SEK is outstanding" in too_much.json()["detail"][0]["msg"]
    assert future.status_code == 422 and future.json()["detail"][0]["type"] == "payment.future_date"
    assert zero.status_code == 422
    assert _pay(client, sales.headers, invoice, "62.50").json()["payment_status"] == "paid"


def test_a_draft_cannot_be_paid(client: TestClient, db_session: Session, sales):
    draft = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing))
    assert draft["payment_status"] is None and draft["paid_amount"] is None
    response = _pay(client, sales.headers, draft, "10.00")
    assert response.status_code == 409 and response.json()["detail"]["code"] == "invoice_not_issued"


def test_a_mistake_is_reversed_by_a_new_row_once_and_the_history_shows_both(client: TestClient, db_session: Session, sales):
    invoice = _issued(client, db_session, sales)
    payment_id = _pay(client, sales.headers, invoice, "1062.50").json()["payments"][0]["id"]
    url = f"/api/invoices/{invoice['id']}/payments/{payment_id}/reverse"

    reversed_ = client.post(url, json={"note": "Wrong invoice"}, headers=sales.headers)
    again = client.post(url, json={}, headers=sales.headers)
    reversal_id = reversed_.json()["payments"][1]["id"]
    of_reversal = client.post(f"/api/invoices/{invoice['id']}/payments/{reversal_id}/reverse", json={}, headers=sales.headers)

    body = reversed_.json()
    assert body["payment_status"] == "unpaid" and body["outstanding_amount"] == "1062.50"
    assert [(p["amount"], p["reversed"], p["reverses_payment_id"]) for p in body["payments"]] == [("1062.50", True, None), ("-1062.50", False, payment_id)]
    assert again.status_code == 409 and again.json()["detail"]["code"] == "payment_already_reversed"
    assert of_reversal.status_code == 409 and of_reversal.json()["detail"]["code"] == "payment_is_reversal"
    actions = [e["action"] for e in client.get("/api/history", params={"entity_type": "invoice", "entity_id": invoice["id"]}, headers=sales.headers).json()["events"]]
    assert {"payment_reversed", "payment_recorded"} <= set(actions)  # same instant in the test session: no fixed order


def test_payments_are_append_only(client: TestClient, db_session: Session, sales):
    invoice = _issued(client, db_session, sales)
    _pay(client, sales.headers, invoice, "10.00")
    for statement in ("UPDATE invoice_payments SET amount = 1", "DELETE FROM invoice_payments"):
        with pytest.raises(DBAPIError, match="append-only"), db_session.begin_nested():
            db_session.execute(text(statement))


@pytest.mark.parametrize("role,allowed", [(Role.ADMIN, True), (Role.ACCOUNTANT, True), (Role.EMPLOYEE, False), (Role.VIEWER, False)])
def test_who_may_record_and_reverse_payments(client: TestClient, db_session: Session, sales, role, allowed):
    invoice = _issued(client, db_session, sales)
    headers = member_of(db_session, sales.org, role)

    response = _pay(client, headers, invoice, "10.00")

    assert response.status_code == (201 if allowed else 403)
    assert client.get(f"/api/invoices/{invoice['id']}", headers=headers).json()["payment_status"] in ("unpaid", "partially_paid")


def test_another_organizations_invoice_cannot_be_paid_or_seen(client: TestClient, db_session: Session):
    two = Two(db_session)
    b_invoice = issue(client, two.b, draft_invoice(client, two.b, completed(db_session, two.b_org, two.b_customer)))
    payment = _pay(client, two.b, b_invoice, "10.00").json()["payments"][0]["id"]

    assert _pay(client, two.a, b_invoice, "10.00").status_code == 404
    assert client.post(f"/api/invoices/{b_invoice['id']}/payments/{payment}/reverse", json={}, headers=two.a).status_code == 404
    assert client.get(f"/api/invoices/{b_invoice['id']}", headers=two.b).json()["paid_amount"] == "10.00"


def test_the_list_filters_by_payment_state_and_the_dashboard_counts_only_what_is_outstanding(client: TestClient, db_session: Session, sales):
    long_ago = today_in(None).replace(day=1) - timedelta(days=40)
    overdue = _issued(client, db_session, sales, invoice_date=str(long_ago), due_date=str(long_ago + timedelta(days=10)))
    settled = _issued(client, db_session, sales, invoice_date=str(long_ago), due_date=str(long_ago + timedelta(days=10)))
    part = _issued(client, db_session, sales)
    _pay(client, sales.headers, settled, "1062.50")
    _pay(client, sales.headers, part, "62.50")

    def ids(**params):
        return {row["id"] for row in client.get("/api/invoices", params=params, headers=sales.headers).json()}

    assert ids(payment="unpaid") == {overdue["id"]}
    assert ids(payment="partially_paid") == {part["id"]}
    assert ids(payment="paid") == {settled["id"]}
    assert ids(payment="open") == {overdue["id"], part["id"]}
    summary = client.get("/api/invoices/summary", headers=sales.headers).json()
    assert summary["past_due"] == {"count": 1, "amounts": [{"currency": "SEK", "amount": "1062.50"}]}  # the paid one is not past due
    assert summary["paid_this_month"] == {"count": 2, "amounts": [{"currency": "SEK", "amount": "1125.00"}]}
