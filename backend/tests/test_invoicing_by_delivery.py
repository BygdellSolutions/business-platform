"""Invoicing by delivery (owner, 2026-10-11): only what was delivered is invoiced; the rest when it is delivered.

The first time an order that is not delivered in full is invoiced, the person chooses: invoice the delivered part now
(`partial`), or wait until everything is delivered (a hold). Units delivered later go onto the customer's open draft
invoice by themselves, for orders that are invoiced by delivery or were waiting and are now complete.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import ItemType, Role
from tests.factories import make_customer, make_item, make_org
from tests.invoicing_support import INVOICEABLE, INVOICES, issue, member_of

TODAY = "2026-10-10"


@pytest.fixture
def shop(db_session: Session):
    org = make_org(db_session)
    owner = member_of(db_session, org, Role.OWNER)
    return SimpleNamespace(
        org=org,
        owner=owner,
        anna=make_customer(db_session, org, "Anna"),
        pad=make_item(db_session, org, "Saddle pad", ItemType.PRODUCT, "pcs", "100.00", track_stock=True),
        massage=make_item(db_session, org, "Massage", ItemType.SERVICE, "h", "850.00"),
    )


def _stock(client: TestClient, shop, quantity: str) -> None:
    assert client.post(f"/api/items/{shop.pad.id}/stock", json={"kind": "count", "quantity": quantity}, headers=shop.owner).status_code == 201


def _order(client: TestClient, shop, *lines, headers=None) -> dict:
    """A completed order of `lines` (item, quantity): stock is delivered and backordered at completion."""
    headers = headers or shop.owner
    tx = client.post("/api/transactions", json={"billing_customer_id": str(shop.anna.id), "transaction_date": TODAY}, headers=headers).json()
    for item, quantity in lines:
        assert client.post(f"/api/transactions/{tx['id']}/lines", json={"item_id": str(item.id), "quantity": quantity}, headers=headers).status_code == 201
    assert client.post(f"/api/transactions/{tx['id']}/complete", headers=headers).status_code == 200
    return client.get(f"/api/transactions/{tx['id']}", headers=headers).json()


def _invoice(client: TestClient, shop, order: dict, **body):
    return client.post(f"{INVOICES}/for-order", json={"transaction_id": order["id"], **body}, headers=shop.owner)


def _deliver_later(client: TestClient, shop, quantity: str, headers=None):
    """The backordered units arrive and a person delivers them (Deliver)."""
    headers = headers or shop.owner
    incoming = client.post("/api/inventory/incoming", json={"item_id": str(shop.pad.id), "quantity": quantity}, headers=shop.owner).json()
    assert client.post(f"/api/inventory/incoming/{incoming['id']}/receive", json={}, headers=shop.owner).status_code == 200
    backorder = client.get("/api/inventory/backorders", headers=shop.owner).json()[0]
    response = client.post(
        f"/api/inventory/items/{shop.pad.id}/allocation", json={"allocations": [{"fulfillment_id": backorder["fulfillment_id"], "quantity": quantity}]}, headers=headers
    )
    assert response.status_code == 200, response.text


def _state(client: TestClient, shop, order: dict) -> dict:
    return client.get(f"{INVOICES}/by-transaction", params={"ids": order["id"]}, headers=shop.owner).json()[0]


def _lines(invoice: dict) -> list[tuple]:
    return [(line["description"], line["quantity"], line["net_amount"], line["gross_amount"]) for line in invoice["lines"]]


# --- the question ----------------------------------------------------------------------------------------------------


def test_invoicing_an_order_not_delivered_in_full_asks_first(client: TestClient, shop):
    _stock(client, shop, "2")
    order = _order(client, shop, (shop.pad, "7"))

    response = _invoice(client, shop, order)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "partial_delivery" and detail["transaction_ids"] == [order["id"]]
    assert detail["lines"] == [{"description": "Saddle pad", "unit": "pcs", "ordered": "7.000", "delivered": "2.000", "backordered": "5.000"}]
    assert client.get(INVOICES, headers=shop.owner).json() == []  # nothing was made


def test_an_order_delivered_in_full_is_invoiced_without_a_question(client: TestClient, shop):
    _stock(client, shop, "9")
    order = _order(client, shop, (shop.pad, "7"))
    invoice = _invoice(client, shop, order).json()
    assert _lines(invoice) == [("Saddle pad", "7.000", "700.00", "875.00")]


# --- invoice what is delivered now -------------------------------------------------------------------------------------


def test_the_delivered_part_is_invoiced_now_and_the_rest_when_it_is_delivered(client: TestClient, shop):
    _stock(client, shop, "2")
    order = _order(client, shop, (shop.pad, "7"), (shop.massage, "1"))

    first = _invoice(client, shop, order, partial=True)

    assert first.status_code == 200, first.text
    first = first.json()
    # The service is "delivered" when performed: invoiced whole. Of the pads, the 2 handed over.
    assert _lines(first) == [("Saddle pad", "2.000", "200.00", "250.00"), ("Massage", "1.000", "850.00", "1062.50")]
    pad = first["lines"][0]
    assert (pad["ordered_quantity"], pad["backordered_quantity"]) == ("7.000", "5.000")
    assert (first["net_amount"], first["gross_amount"]) == ("1050.00", "1312.50")
    assert _state(client, shop, order)["state"] == "draft"
    issue(client, shop.owner, first)
    assert _state(client, shop, order)["state"] == "partly"

    # The 5 arrive and are delivered: they go onto a new draft for Anna by themselves.
    _deliver_later(client, shop, "5")

    drafts = client.get(INVOICES, params={"status": "draft"}, headers=shop.owner).json()
    assert len(drafts) == 1
    second = client.get(f"{INVOICES}/{drafts[0]['id']}", headers=shop.owner).json()
    assert _lines(second) == [("Saddle pad", "5.000", "500.00", "625.00")]
    assert (second["lines"][0]["ordered_quantity"], second["lines"][0]["backordered_quantity"]) == ("7.000", "0.000")
    issue(client, shop.owner, second)
    state = _state(client, shop, order)
    assert state["state"] == "invoiced" and [entry["status"] for entry in state["invoices"]] == ["issued", "issued"]


def test_a_part_is_priced_per_line_half_up_like_every_line(client: TestClient, db_session: Session, shop):
    odd = make_item(db_session, shop.org, "Liniment", ItemType.PRODUCT, "ml", "0.35", track_stock=True)
    assert client.post(f"/api/items/{odd.id}/stock", json={"kind": "count", "quantity": "3"}, headers=shop.owner).status_code == 201
    order = _order(client, shop, (odd, "7"))
    invoice = _invoice(client, shop, order, partial=True).json()
    # 3 × 0.35 = 1.05; VAT 25 % of 1.05 = 0.2625 → 0.26 (half-up on the line, as for any line).
    assert [(line["quantity"], line["net_amount"], line["vat_amount"], line["gross_amount"]) for line in invoice["lines"]] == [("3.000", "1.05", "0.26", "1.31")]


def test_nothing_delivered_yet_means_nothing_to_invoice(client: TestClient, shop):
    order = _order(client, shop, (shop.pad, "4"))  # nothing on hand: all 4 backordered
    response = _invoice(client, shop, order, partial=True)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "nothing_to_invoice"
    assert client.get(INVOICES, headers=shop.owner).json() == []
    assert client.get(INVOICEABLE, headers=shop.owner).json() == []  # not ready to invoice either


def test_ready_to_invoice_is_what_was_delivered_and_not_invoiced(client: TestClient, shop):
    _stock(client, shop, "2")
    order = _order(client, shop, (shop.pad, "7"))

    ready = client.get(INVOICEABLE, headers=shop.owner).json()
    assert [(row["id"], row["totals"]["gross_amount"]) for row in ready] == [(order["id"], "250.00")]  # 2 of 7
    summary = client.get(f"{INVOICES}/summary", headers=shop.owner).json()["ready_to_invoice"]
    assert summary["count"] == 1 and summary["amounts"] == [{"currency": "SEK", "amount": "250.00"}]

    _invoice(client, shop, order, partial=True)
    assert client.get(INVOICEABLE, headers=shop.owner).json() == []  # on a draft


def test_an_order_is_never_on_two_drafts(client: TestClient, shop):
    _stock(client, shop, "2")
    order = _order(client, shop, (shop.pad, "7"))
    _invoice(client, shop, order, partial=True)
    again = client.post(INVOICES, json={"transaction_ids": [order["id"]]}, headers=shop.owner)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "already_invoiced"


# --- wait until everything is delivered ----------------------------------------------------------------------------------


def test_waiting_until_everything_is_delivered_invoices_the_whole_order_at_once(client: TestClient, shop):
    _stock(client, shop, "2")
    order = _order(client, shop, (shop.pad, "7"))

    assert client.post(f"{INVOICES}/holds", json={"transaction_id": order["id"]}, headers=shop.owner).status_code == 204

    assert _state(client, shop, order)["state"] == "waiting"
    asked = _invoice(client, shop, order)
    assert asked.status_code == 409 and asked.json()["detail"]["code"] == "waiting_for_delivery"
    assert client.get(INVOICEABLE, headers=shop.owner).json() == []

    _deliver_later(client, shop, "5")  # now everything is delivered

    drafts = client.get(INVOICES, params={"status": "draft"}, headers=shop.owner).json()
    invoice = client.get(f"{INVOICES}/{drafts[0]['id']}", headers=shop.owner).json()
    assert _lines(invoice) == [("Saddle pad", "7.000", "700.00", "875.00")]


def test_a_waiting_order_can_still_be_invoiced_in_part_and_then_is_not_waiting(client: TestClient, shop):
    _stock(client, shop, "2")
    order = _order(client, shop, (shop.pad, "7"))
    client.post(f"{INVOICES}/holds", json={"transaction_id": order["id"]}, headers=shop.owner)

    invoice = _invoice(client, shop, order, partial=True).json()

    assert _lines(invoice) == [("Saddle pad", "2.000", "200.00", "250.00")]
    assert _state(client, shop, order)["state"] == "draft"
    client.delete(f"{INVOICES}/{invoice['id']}", headers={**shop.owner, "If-Match": f'"{invoice["version"]}"'})
    assert _state(client, shop, order)["state"] == "none"  # the hold was ended by choosing to invoice in part


def test_releasing_the_hold_makes_the_delivered_part_invoiceable_again(client: TestClient, shop):
    _stock(client, shop, "2")
    order = _order(client, shop, (shop.pad, "7"))
    client.post(f"{INVOICES}/holds", json={"transaction_id": order["id"]}, headers=shop.owner)
    assert client.delete(f"{INVOICES}/holds/{order['id']}", headers=shop.owner).status_code == 204
    assert [row["id"] for row in client.get(INVOICEABLE, headers=shop.owner).json()] == [order["id"]]


# --- who may, and what is never touched ----------------------------------------------------------------------------------


def test_a_delivery_by_someone_who_may_not_invoice_waits_in_ready_to_invoice(client: TestClient, db_session: Session, shop):
    employee = member_of(db_session, shop.org, Role.EMPLOYEE)
    _stock(client, shop, "2")
    order = _order(client, shop, (shop.pad, "7"))
    issue(client, shop.owner, _invoice(client, shop, order, partial=True).json())

    _deliver_later(client, shop, "5", headers=employee)

    assert client.get(INVOICES, params={"status": "draft"}, headers=shop.owner).json() == []
    ready = client.get(INVOICEABLE, headers=shop.owner).json()
    assert [(row["id"], row["totals"]["gross_amount"]) for row in ready] == [(order["id"], "625.00")]


def test_a_later_delivery_of_an_order_nobody_invoiced_yet_is_not_invoiced_for_them(client: TestClient, shop):
    _stock(client, shop, "2")
    order = _order(client, shop, (shop.pad, "7"))  # completed, nobody chose how to invoice it
    _deliver_later(client, shop, "5")
    assert client.get(INVOICES, headers=shop.owner).json() == []
    assert [row["id"] for row in client.get(INVOICEABLE, headers=shop.owner).json()] == [order["id"]]


def test_holds_and_the_question_never_reach_another_organization(client: TestClient, db_session: Session, shop):
    _stock(client, shop, "2")
    order = _order(client, shop, (shop.pad, "7"))
    outsider = member_of(db_session, make_org(db_session), Role.OWNER)

    assert client.post(f"{INVOICES}/holds", json={"transaction_id": order["id"]}, headers=outsider).status_code == 404
    assert client.delete(f"{INVOICES}/holds/{order['id']}", headers=outsider).status_code == 404
    assert client.post(f"{INVOICES}/for-order", json={"transaction_id": order["id"], "partial": True}, headers=outsider).status_code == 422
    assert client.get(f"{INVOICES}/by-transaction", params={"ids": order["id"]}, headers=outsider).json()[0]["state"] == "none"
    assert _state(client, shop, order)["state"] == "none"


def test_only_invoicing_roles_choose(client: TestClient, db_session: Session, shop):
    _stock(client, shop, "2")
    order = _order(client, shop, (shop.pad, "7"))
    employee = member_of(db_session, shop.org, Role.EMPLOYEE)
    assert client.post(f"{INVOICES}/holds", json={"transaction_id": order["id"]}, headers=employee).status_code == 403
    assert client.post(f"{INVOICES}/for-order", json={"transaction_id": order["id"], "partial": True}, headers=employee).status_code == 403


def test_a_draft_from_before_that_charges_undelivered_units_is_not_issued(client: TestClient, db_session: Session, shop):
    _stock(client, shop, "2")
    order = _order(client, shop, (shop.pad, "7"))
    draft = _invoice(client, shop, order, partial=True).json()
    # As a draft made before invoicing by delivery: the whole line (inside this rolled-back transaction).
    db_session.execute(text("alter table invoice_lines disable trigger user"))
    db_session.execute(
        text("update invoice_lines set quantity = 7, net_amount = 700.00, vat_amount = 175.00, gross_amount = 875.00, ordered_quantity = NULL, backordered_quantity = 0 where invoice_id = :i"),
        {"i": draft["id"]},
    )
    db_session.execute(text("alter table invoice_lines enable trigger user"))
    db_session.execute(text("alter table invoice_vat_rows disable trigger user"))
    db_session.execute(text("update invoice_vat_rows set net_amount = 700.00, vat_amount = 175.00 where invoice_id = :i"), {"i": draft["id"]})
    db_session.execute(text("alter table invoice_vat_rows enable trigger user"))
    db_session.execute(text("alter table invoices disable trigger user"))
    db_session.execute(text("update invoices set net_amount = 700.00, vat_amount = 175.00, gross_amount = 875.00 where id = :i"), {"i": draft["id"]})
    db_session.execute(text("alter table invoices enable trigger user"))
    db_session.execute(text("alter table invoice_transactions disable trigger user"))
    db_session.execute(text("update invoice_transactions set completes_order = true where invoice_id = :i"), {"i": draft["id"]})
    db_session.execute(text("alter table invoice_transactions enable trigger user"))

    response = client.post(f"{INVOICES}/{draft['id']}/issue", headers={**shop.owner, "If-Match": f'"{draft["version"]}"'})

    assert response.status_code == 409 and response.json()["detail"]["code"] == "more_than_delivered"


# --- the paper invoice (template 11) ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(("language", "printed"), [(None, "Ordered 7 · Backordered 5"), ("sv", "Beställt 7 · Restnoterat 5")])
def test_the_pdf_says_what_was_ordered_and_what_is_backordered(client: TestClient, db_session: Session, shop, language, printed):
    from tests.pdf_support import pdf_text

    shop.org.document_language = language
    db_session.flush()
    _stock(client, shop, "2")
    order = _order(client, shop, (shop.pad, "7"), (shop.massage, "1"))
    invoice = issue(client, shop.owner, _invoice(client, shop, order, partial=True).json())

    text_ = pdf_text(client.get(f"{INVOICES}/{invoice['id']}/pdf", headers=shop.owner).content)

    assert printed in text_
    assert text_.count("·") >= 1 and ("Ordered 1" not in text_ and "Beställt 1" not in text_)  # the whole service line says nothing


# --- credit notes across the invoices of one order line ------------------------------------------------------------------


def _credit(client: TestClient, shop, invoice: dict, line_id: str, quantity: str, returned: bool):
    body = {"reason": "Returned", "lines": [{"invoice_line_id": line_id, "quantity": quantity, "returned_to_stock": returned}]}
    return client.post(f"{INVOICES}/{invoice['id']}/credit-notes", json=body, headers={**shop.owner, "If-Match": f'"{invoice["version"]}"'})


def test_each_invoice_of_a_split_line_credits_and_returns_its_own_units(client: TestClient, shop):
    _stock(client, shop, "2")
    order = _order(client, shop, (shop.pad, "7"))
    first = issue(client, shop.owner, _invoice(client, shop, order, partial=True).json())
    _deliver_later(client, shop, "5")
    draft = client.get(INVOICES, params={"status": "draft"}, headers=shop.owner).json()[0]
    second = issue(client, shop.owner, client.get(f"{INVOICES}/{draft['id']}", headers=shop.owner).json())

    # Each invoice offers its own units back to stock, not the whole order line's 7.
    assert first["lines"][0]["stock_returnable"] == "2.000"
    assert second["lines"][0]["stock_returnable"] == "5.000"

    assert _credit(client, shop, first, first["lines"][0]["id"], "2", True).status_code == 201
    assert _credit(client, shop, second, second["lines"][0]["id"], "5", True).status_code == 201
    assert client.get(f"/api/items/{shop.pad.id}/stock", headers=shop.owner).json()["on_hand"] == "7.000"  # all 7 back

    again = client.get(f"{INVOICES}/{second['id']}", headers=shop.owner).json()
    assert again["lines"][0]["stock_returnable"] == "0.000"
    assert _credit(client, shop, again, again["lines"][0]["id"], "1", True).status_code == 422  # nothing left to credit
