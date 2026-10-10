"""Counter sales (2026-10-10): a draft order is either paid now (completed + paid, with a receipt, never invoiced) or
invoiced (completed and put on the customer's open draft invoice, or a new one). A walk-in customer is never invoiced.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ItemType, Role
from tests.factories import make_customer, make_item, make_org
from tests.invoicing_support import member_of


def _draft(client: TestClient, headers, customer_id, *lines) -> dict:
    tx = client.post("/api/transactions", json={"billing_customer_id": str(customer_id), "transaction_date": "2026-10-10"}, headers=headers).json()
    for body in lines or ({"description": "Massage", "unit": "h", "quantity": "1", "unit_price_ex_vat": "800.00", "vat_rate": "25.00"},):
        assert client.post(f"/api/transactions/{tx['id']}/lines", json=body, headers=headers).status_code == 201
    return client.get(f"/api/transactions/{tx['id']}", headers=headers).json()


def _pay(client, headers, tx, method="swish"):
    return client.post(f"/api/transactions/{tx['id']}/pay-now", json={"method": method}, headers={**headers, "If-Match": f'"{tx["version"]}"'})


@pytest.fixture
def world(db_session: Session):
    org = make_org(db_session)
    return org, member_of(db_session, org, Role.OWNER), make_customer(db_session, org, "Anna")


def test_paid_now_completes_records_the_payment_and_numbers_receipts_from_1001(client: TestClient, db_session: Session, world):
    org, owner, anna = world
    first = _pay(client, owner, _draft(client, owner, anna.id))
    second = _pay(client, owner, _draft(client, owner, anna.id), "card")

    assert first.status_code == 200, first.text
    body = first.json()
    assert (body["status"], body["payment_method"], body["receipt_number_text"]) == ("completed", "swish", "1001")
    assert body["paid_at"] is not None
    assert (second.json()["payment_method"], second.json()["receipt_number_text"]) == ("card", "1002")
    assert _pay(client, owner, _draft(client, owner, anna.id), "bitcoin").status_code == 422


def test_a_paid_order_cannot_be_reopened_cancelled_or_invoiced(client: TestClient, db_session: Session, world):
    org, owner, anna = world
    paid = _pay(client, owner, _draft(client, owner, anna.id)).json()
    headers = {**owner, "If-Match": f'"{paid["version"]}"'}

    for verb in ("reopen", "cancel"):
        refused = client.post(f"/api/transactions/{paid['id']}/{verb}", headers=headers)
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "paid_at_counter"
    assert paid["id"] not in {row["id"] for row in client.get("/api/invoiceable-transactions", headers=owner).json()}
    invoiced = client.post("/api/invoices", json={"transaction_ids": [paid["id"]]}, headers=owner)
    assert invoiced.status_code == 409 and invoiced.json()["detail"]["code"] == "paid_at_counter"
    # The database refuses it too, whatever path tries.
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.execute(text("update transactions set status = 'draft' where id = :i"), {"i": paid["id"]})


def test_paid_now_does_what_completion_does_stock_leaves_the_shelf(client: TestClient, db_session: Session, world):
    org, owner, anna = world
    product = make_item(db_session, org, name="Liniment", type=ItemType.PRODUCT, unit="pcs", price_ex_vat="100.00", track_stock=True)
    client.post(f"/api/items/{product.id}/stock", json={"kind": "count", "quantity": "5", "note": "Opening stock"}, headers=owner)
    tx = _draft(client, owner, anna.id, {"item_id": str(product.id), "quantity": "2"})
    assert _pay(client, owner, tx).status_code == 200
    figures = client.get("/api/inventory/availability", params={"item_id": str(product.id)}, headers=owner).json()[0]
    assert figures["on_hand"] == "3.000"


def test_the_walk_in_customer_is_one_per_organization_and_never_invoiced(client: TestClient, db_session: Session, world):
    org, owner, _ = world
    first = client.post("/api/customers/walk-in", headers=owner).json()
    again = client.post("/api/customers/walk-in", headers=owner).json()
    assert first["id"] == again["id"] and (first["name"], first["walk_in"]) == ("Walk-in customer", True)
    other_org = make_org(db_session)
    other = client.post("/api/customers/walk-in", headers=member_of(db_session, other_org, Role.OWNER)).json()
    assert other["id"] != first["id"]  # each organization has its own

    tx = _draft(client, owner, first["id"])
    assert tx["billing_customer"]["walk_in"] is True
    done = client.post(f"/api/transactions/{tx['id']}/complete", headers={**owner, "If-Match": f'"{tx["version"]}"'}).json()
    refused = client.post("/api/invoices/for-order", json={"transaction_id": done["id"]}, headers=owner)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "walk_in_customer"
    assert done["id"] not in {row["id"] for row in client.get("/api/invoiceable-transactions", headers=owner).json()}
    assert _pay(client, owner, _draft(client, owner, first["id"])).status_code == 200  # paying at the counter is the way


def test_invoice_collects_a_customers_orders_on_one_open_draft(client: TestClient, db_session: Session, world):
    org, owner, anna = world
    bo = make_customer(db_session, org, "Bo")

    def complete(customer_id):
        tx = _draft(client, owner, customer_id)
        return client.post(f"/api/transactions/{tx['id']}/complete", headers={**owner, "If-Match": f'"{tx["version"]}"'}).json()

    first = client.post("/api/invoices/for-order", json={"transaction_id": complete(anna.id)["id"]}, headers=owner)
    assert first.status_code == 200, first.text
    client.patch(f"/api/invoices/{first.json()['id']}", json={"description": "October"}, headers={**owner, "If-Match": f'"{first.json()["version"]}"'})
    second = client.post("/api/invoices/for-order", json={"transaction_id": complete(anna.id)["id"]}, headers=owner).json()
    other = client.post("/api/invoices/for-order", json={"transaction_id": complete(bo.id)["id"]}, headers=owner).json()

    assert len(second["transactions"]) == 2 and second["description"] == "October"  # the same draft, header kept
    assert second["gross_amount"] == "2000.00"
    assert len(other["transactions"]) == 1 and other["customer_name"] == "Bo"  # another customer: its own draft
    drafts = client.get("/api/invoices", params={"status": "draft"}, headers=owner).json()
    assert sorted(len(d) for d in [[r for r in drafts if r["customer_name"] == name] for name in ("Anna", "Bo")]) == [1, 1]

    # Once issued, the next order starts a new draft.
    from tests.invoicing_support import issue

    issue(client, owner, second)
    third = client.post("/api/invoices/for-order", json={"transaction_id": complete(anna.id)["id"]}, headers=owner).json()
    assert third["status"] == "draft" and len(third["transactions"]) == 1


def test_invoicing_an_order_of_another_organization_is_not_found(client: TestClient, db_session: Session, world):
    org, owner, anna = world
    other_org = make_org(db_session)
    other_owner = member_of(db_session, other_org, Role.OWNER)
    theirs = _draft(client, other_owner, make_customer(db_session, other_org, "Anna").id)
    response = client.post("/api/invoices/for-order", json={"transaction_id": theirs["id"]}, headers=owner)
    assert response.status_code == 422 and response.json()["detail"][0]["type"] == "reference.not_found"
    assert client.post(f"/api/transactions/{theirs['id']}/pay-now", json={"method": "cash"}, headers={**owner, "If-Match": '"1"'}).status_code == 404


def test_a_paid_order_has_a_receipt_pdf_and_nothing_else_does(client: TestClient, db_session: Session, world):
    from tests.pdf_support import pdf_text

    org, owner, anna = world
    paid = _pay(client, owner, _draft(client, owner, anna.id), "card").json()
    response = client.get(f"/api/invoices/receipts/{paid['id']}/pdf", headers=owner)
    assert response.status_code == 200 and response.headers["content-type"] == "application/pdf"
    assert "receipt-1001.pdf" in response.headers["content-disposition"]
    text = pdf_text(response.content)
    assert "Receipt" in text and "1001" in text and "Paid by card" in text and "1 000.00 SEK" in text
    assert "Bankgiro" not in text and "Due date" not in text  # nothing to pay

    unpaid = _draft(client, owner, anna.id)
    refused = client.get(f"/api/invoices/receipts/{unpaid['id']}/pdf", headers=owner)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "not_paid_at_counter"
    outsider = member_of(db_session, make_org(db_session), Role.OWNER)
    assert client.get(f"/api/invoices/receipts/{paid['id']}/pdf", headers=outsider).status_code == 404


def test_the_dashboard_counts_counter_sales_as_paid_and_never_as_ready_to_invoice(client: TestClient, db_session: Session, world):
    org, owner, anna = world
    _pay(client, owner, _draft(client, owner, anna.id))  # 1000.00 incl. VAT
    walk_in = client.post("/api/customers/walk-in", headers=owner).json()
    open_walk_in = _draft(client, owner, walk_in["id"])
    client.post(f"/api/transactions/{open_walk_in['id']}/complete", headers={**owner, "If-Match": f'"{open_walk_in["version"]}"'})

    summary = client.get("/api/invoices/summary", headers=owner).json()
    assert summary["counter_sales_this_month"] == {"count": 1, "amounts": [{"currency": "SEK", "amount": "1000.00"}]}
    assert summary["paid_this_month"] == {"count": 1, "amounts": [{"currency": "SEK", "amount": "1000.00"}]}
    assert summary["counter_sales_this_year"]["count"] == 1
    assert summary["ready_to_invoice"]["count"] == 0  # neither the paid one nor the walk-in one
