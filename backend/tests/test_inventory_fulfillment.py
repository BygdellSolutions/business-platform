"""Inventory I3: completion delivers what is available and backorders the shortage; reopen and cancel give it back.

Physical stock never goes negative; units promised to open backorders are not available to a newer sale; every
delivery and return is a movement in the ledger, and nothing earlier is changed or deleted.
"""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ItemType, Role
from app.modules.inventory.models import LineFulfillment, StockMovement
from tests.factories import add_member, make_customer, make_item, make_line, make_org, make_transaction, make_user


def _world(db: Session):
    org = make_org(db)
    user = make_user(db)
    add_member(db, org, user, Role.OWNER)
    return org, {"X-Dev-User-Email": user.email}


def _product(db: Session, org, name="Liniment"):
    return make_item(db, org, name=name, type=ItemType.PRODUCT, unit="pcs", price_ex_vat="120.00", track_stock=True)


def _count(client: TestClient, item, headers, quantity: str):
    assert client.post(f"/api/items/{item.id}/stock", json={"kind": "count", "quantity": quantity, "note": "Count"}, headers=headers).status_code == 201


def _sale(db: Session, org, *quantities, item=None, position=1):
    tx = make_transaction(db, org, billing_customer=make_customer(db, org))
    for offset, quantity in enumerate(quantities):
        make_line(db, org, tx, item=item, description="Liniment", unit="pcs", quantity=quantity, unit_price_ex_vat="120.00", position=position + offset)
    return tx


def _on_hand(client: TestClient, item, headers) -> str:
    return client.get(f"/api/items/{item.id}/stock", headers=headers).json()["on_hand"]


def _fulfillment(client: TestClient, tx, headers):
    return client.get(f"/api/inventory/transactions/{tx.id}/fulfillment", headers=headers).json()


def test_completion_delivers_what_is_available_in_line_order_and_backorders_the_rest(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    item = _product(db_session, org)
    _count(client, item, owner, "5")
    tx = _sale(db_session, org, "3", "4", item=item)

    assert client.post(f"/api/transactions/{tx.id}/complete", headers=owner).status_code == 200

    rows = sorted(_fulfillment(client, tx, owner), key=lambda row: row["ordered"])
    assert [(r["ordered"], r["delivered"], r["backordered"], r["remaining"], r["state"]) for r in rows] == [
        ("3.000", "3.000", "0.000", "0.000", "fulfilled"),
        ("4.000", "2.000", "2.000", "2.000", "waiting_for_stock"),
    ]
    assert _on_hand(client, item, owner) == "0.000"  # never below zero
    movements = client.get(f"/api/items/{item.id}/stock", headers=owner).json()["movements"]
    assert [(m["reason"], m["quantity_change"], m["transaction_id"]) for m in movements[:2]] == [("delivery", "-2.000", str(tx.id)), ("delivery", "-3.000", str(tx.id))]


def test_lines_without_stock_tracking_are_left_alone(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    untracked = make_item(db_session, org, name="Plain product", type=ItemType.PRODUCT)
    service = make_item(db_session, org)
    tx = _sale(db_session, org, "2", item=untracked)
    make_line(db_session, org, tx, item=service, position=2)
    make_line(db_session, org, tx, item=None, description="Ad hoc", position=3)

    assert client.post(f"/api/transactions/{tx.id}/complete", headers=owner).status_code == 200
    assert _fulfillment(client, tx, owner) == []
    assert db_session.scalar(select(StockMovement.id).where(StockMovement.organization_id == org.id)) is None


def test_promised_units_are_not_available_to_a_newer_sale(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    item = _product(db_session, org)
    first = _sale(db_session, org, "2", item=item)
    client.post(f"/api/transactions/{first.id}/complete", headers=owner)  # nothing on hand: 2 backordered
    _count(client, item, owner, "1")  # one arrives (counted by hand)

    availability = client.get("/api/inventory/availability", params={"item_id": str(item.id)}, headers=owner).json()
    second = _sale(db_session, org, "1", item=item)
    client.post(f"/api/transactions/{second.id}/complete", headers=owner)

    assert availability == [{"item_id": str(item.id), "on_hand": "1.000", "committed": "2.000", "available": "0.000", "incoming": "0.000", "low_stock_threshold": None, "states": ["backordered"]}]
    assert [(r["delivered"], r["backordered"]) for r in _fulfillment(client, second, owner)] == [("0.000", "1.000")]
    assert _fulfillment(client, first, owner)[0]["state"] == "ready_to_fulfill"  # stock is there; a person allocates it (I5)
    assert _on_hand(client, item, owner) == "1.000"


def test_reopen_returns_the_delivered_units_and_completing_again_starts_afresh(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    item = _product(db_session, org)
    _count(client, item, owner, "5")
    tx = _sale(db_session, org, "3", item=item)
    client.post(f"/api/transactions/{tx.id}/complete", headers=owner)

    assert client.post(f"/api/transactions/{tx.id}/reopen", headers=owner).status_code == 200

    assert _on_hand(client, item, owner) == "5.000" and _fulfillment(client, tx, owner) == []
    movements = client.get(f"/api/items/{item.id}/stock", headers=owner).json()["movements"]
    assert [(m["reason"], m["quantity_change"], m["note"]) for m in movements[:2]] == [("return", "3.000", "Transaction reopened"), ("delivery", "-3.000", None)]
    old = db_session.scalars(select(LineFulfillment).where(LineFulfillment.transaction_id == tx.id)).all()
    assert [(row.cancel_reason, row.cancelled_by is not None) for row in old] == [("reopen", True)]  # kept, not deleted

    client.post(f"/api/transactions/{tx.id}/complete", headers=owner)
    assert [(r["delivered"], r["state"]) for r in _fulfillment(client, tx, owner)] == [("3.000", "fulfilled")]
    assert _on_hand(client, item, owner) == "2.000"


def test_cancelling_a_completed_transaction_returns_stock_and_cancels_its_backorders(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    item = _product(db_session, org)
    _count(client, item, owner, "1")
    tx = _sale(db_session, org, "3", item=item)
    client.post(f"/api/transactions/{tx.id}/complete", headers=owner)

    assert client.post(f"/api/transactions/{tx.id}/cancel", headers=owner).status_code == 200

    assert _on_hand(client, item, owner) == "1.000"
    available = client.get("/api/inventory/availability", params={"item_id": str(item.id)}, headers=owner).json()[0]["available"]
    assert available == "1.000"  # the cancelled backorder no longer holds anything
    row = db_session.scalar(select(LineFulfillment).where(LineFulfillment.transaction_id == tx.id))
    assert row.cancel_reason == "cancel"


def test_cancelling_a_draft_moves_no_stock(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    item = _product(db_session, org)
    _count(client, item, owner, "5")
    tx = _sale(db_session, org, "3", item=item)

    assert client.post(f"/api/transactions/{tx.id}/cancel", headers=owner).status_code == 200
    assert _on_hand(client, item, owner) == "5.000"
    assert len(client.get(f"/api/items/{item.id}/stock", headers=owner).json()["movements"]) == 1


def test_an_invoiced_transaction_cannot_be_reopened_so_its_stock_stays_delivered(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    item = _product(db_session, org)
    _count(client, item, owner, "5")
    tx = _sale(db_session, org, "3", item=item)
    client.post(f"/api/transactions/{tx.id}/complete", headers=owner)
    assert client.post("/api/invoices", json={"transaction_ids": [str(tx.id)]}, headers=owner).status_code == 201

    assert client.post(f"/api/transactions/{tx.id}/reopen", headers=owner).status_code == 409  # Invoicing's veto comes first
    assert _on_hand(client, item, owner) == "2.000"
    assert [r["delivered"] for r in _fulfillment(client, tx, owner)] == ["3.000"]


def test_another_organizations_fulfillment_is_not_found(client: TestClient, db_session: Session):
    _, owner_a = _world(db_session)
    org_b, _ = _world(db_session)
    tx_b = _sale(db_session, org_b, "1")

    assert client.get(f"/api/inventory/transactions/{tx_b.id}/fulfillment", headers=owner_a).status_code == 404
