"""Inventory I4: incoming stock and goods receipt.

Incoming is never on hand: a person receives it (all or part), and each receipt is a movement naming the delivery.
What is left can be cancelled; received units stay. A receipt fulfills no backorder by itself: waiting backorders
become "ready to fulfill" for a person to allocate (I5).
"""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import ItemType, Role
from tests.factories import add_member, make_customer, make_item, make_line, make_org, make_transaction, make_user


def _world(db: Session, role: Role = Role.OWNER):
    org = make_org(db)
    user = make_user(db)
    add_member(db, org, user, role)
    return org, {"X-Dev-User-Email": user.email}


def _product(db: Session, org):
    return make_item(db, org, name="Liniment", type=ItemType.PRODUCT, unit="pcs", price_ex_vat="120.00", track_stock=True)


def _availability(client: TestClient, item, headers):
    return client.get("/api/inventory/availability", params={"item_id": str(item.id)}, headers=headers).json()[0]


def test_incoming_stock_is_on_its_way_until_a_person_receives_it(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    item = _product(db_session, org)

    created = client.post(
        "/api/inventory/incoming",
        json={"item_id": str(item.id), "quantity": "10", "expected_on": "2026-10-20", "supplier": "Horse Supplies AB", "reference": "PO-17"},
        headers=owner,
    )
    assert created.status_code == 201 and created.json()["state"] == "expected" and created.json()["created_by_name"]
    figures = _availability(client, item, owner)
    assert (figures["on_hand"], figures["available"], figures["incoming"]) == ("0.000", "0.000", "10.000")

    incoming_id = created.json()["id"]
    part = client.post(f"/api/inventory/incoming/{incoming_id}/receive", json={"quantity": "4", "note": "First pallet"}, headers=owner)
    too_much = client.post(f"/api/inventory/incoming/{incoming_id}/receive", json={"quantity": "7"}, headers=owner)
    rest = client.post(f"/api/inventory/incoming/{incoming_id}/receive", json={}, headers=owner)

    assert part.json()["state"] == "partially_received" and part.json()["remaining"] == "6.000"
    assert too_much.status_code == 422 and too_much.json()["detail"][0]["type"] == "incoming.too_much"
    assert rest.json()["state"] == "received" and rest.json()["remaining"] == "0.000"
    assert client.post(f"/api/inventory/incoming/{incoming_id}/receive", json={}, headers=owner).status_code == 409
    figures = _availability(client, item, owner)
    assert (figures["on_hand"], figures["incoming"]) == ("10.000", "0.000")
    movements = client.get(f"/api/items/{item.id}/stock", headers=owner).json()["movements"]
    assert [(m["reason"], m["quantity_change"], m["note"]) for m in movements] == [("receipt", "6.000", None), ("receipt", "4.000", "First pallet")]
    assert client.get("/api/inventory/incoming", headers=owner).json() == []  # nothing open any more
    assert len(client.get("/api/inventory/incoming", params={"open_only": "false"}, headers=owner).json()) == 1


def test_cancelling_keeps_what_was_received(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    item = _product(db_session, org)
    incoming_id = client.post("/api/inventory/incoming", json={"item_id": str(item.id), "quantity": "5"}, headers=owner).json()["id"]
    client.post(f"/api/inventory/incoming/{incoming_id}/receive", json={"quantity": "2"}, headers=owner)

    cancelled = client.post(f"/api/inventory/incoming/{incoming_id}/cancel", headers=owner)

    assert cancelled.json()["state"] == "cancelled" and cancelled.json()["remaining"] == "0.000"
    figures = _availability(client, item, owner)
    assert (figures["on_hand"], figures["incoming"]) == ("2.000", "0.000")
    assert client.post(f"/api/inventory/incoming/{incoming_id}/receive", json={}, headers=owner).status_code == 409


def test_a_receipt_makes_backorders_ready_but_fulfills_nothing_by_itself(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    item = _product(db_session, org)
    tx = make_transaction(db_session, org, billing_customer=make_customer(db_session, org))
    make_line(db_session, org, tx, item=item, description="Liniment", unit="pcs", quantity="3", unit_price_ex_vat="120.00")
    client.post(f"/api/transactions/{tx.id}/complete", headers=owner)  # nothing on hand: 3 backordered
    incoming_id = client.post("/api/inventory/incoming", json={"item_id": str(item.id), "quantity": "3"}, headers=owner).json()["id"]

    client.post(f"/api/inventory/incoming/{incoming_id}/receive", json={}, headers=owner)

    [row] = client.get(f"/api/inventory/transactions/{tx.id}/fulfillment", headers=owner).json()
    assert (row["state"], row["fulfilled_later"], row["remaining"]) == ("ready_to_fulfill", "0.000", "3.000")
    assert _availability(client, item, owner)["available"] == "0.000"  # promised to the waiting sale


def test_only_stock_tracking_items_of_this_organization_can_be_expected(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    other_org, other_owner = _world(db_session)
    untracked = make_item(db_session, org, name="Plain", type=ItemType.PRODUCT)
    foreign = _product(db_session, other_org)
    foreign_incoming = client.post("/api/inventory/incoming", json={"item_id": str(foreign.id), "quantity": "1"}, headers=other_owner).json()["id"]

    untracked_answer = client.post("/api/inventory/incoming", json={"item_id": str(untracked.id), "quantity": "1"}, headers=owner)
    foreign_answer = client.post("/api/inventory/incoming", json={"item_id": str(foreign.id), "quantity": "1"}, headers=owner)

    assert untracked_answer.json()["detail"][0]["type"] == "stock.not_tracked"
    assert foreign_answer.json()["detail"][0]["type"] == "reference.not_found"
    assert client.post(f"/api/inventory/incoming/{foreign_incoming}/receive", json={}, headers=owner).status_code == 404
    assert client.post(f"/api/inventory/incoming/{foreign_incoming}/cancel", headers=owner).status_code == 404
    assert client.get("/api/inventory/incoming", headers=owner).json() == []


def test_a_viewer_sees_incoming_stock_but_cannot_record_or_receive_it(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    viewer = make_user(db_session)
    add_member(db_session, org, viewer, Role.VIEWER)
    headers = {"X-Dev-User-Email": viewer.email}
    item = _product(db_session, org)
    incoming_id = client.post("/api/inventory/incoming", json={"item_id": str(item.id), "quantity": "5"}, headers=owner).json()["id"]

    assert len(client.get("/api/inventory/incoming", headers=headers).json()) == 1
    assert client.post("/api/inventory/incoming", json={"item_id": str(item.id), "quantity": "1"}, headers=headers).status_code == 403
    assert client.post(f"/api/inventory/incoming/{incoming_id}/receive", json={}, headers=headers).status_code == 403
    assert client.post(f"/api/inventory/incoming/{incoming_id}/cancel", headers=headers).status_code == 403


def test_stock_states_are_separate_and_combine(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    item = make_item(db_session, org, name="Fly spray", type=ItemType.PRODUCT, unit="pcs", track_stock=True)
    assert client.patch(f"/api/items/{item.id}", json={"low_stock_threshold": "5"}, headers=owner).json()["low_stock_threshold"] == "5.000"
    states = lambda: _availability(client, item, owner)["states"]  # noqa: E731

    assert states() == ["out_of_stock"]
    client.post(f"/api/items/{item.id}/stock", json={"kind": "count", "quantity": "3"}, headers=owner)
    assert states() == ["low_stock"]
    client.post("/api/inventory/incoming", json={"item_id": str(item.id), "quantity": "10"}, headers=owner)
    assert states() == ["low_stock", "incoming"]
    tx = make_transaction(db_session, org, billing_customer=make_customer(db_session, org))
    make_line(db_session, org, tx, item=item, description="Fly spray", unit="pcs", quantity="4", unit_price_ex_vat="120.00")
    client.post(f"/api/transactions/{tx.id}/complete", headers=owner)  # 3 delivered, 1 backordered
    assert states() == ["out_of_stock", "backordered", "incoming"]
    assert client.patch(f"/api/items/{item.id}", json={"low_stock_threshold": "-1"}, headers=owner).status_code == 422


def test_the_inventory_lists_every_stock_tracking_product_with_its_figures(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    other, other_owner = _world(db_session)
    shelf = make_item(db_session, org, name="Hoof oil", type=ItemType.PRODUCT, unit="pcs", track_stock=True, sku="HO-1")
    empty = make_item(db_session, org, name="Fly spray", type=ItemType.PRODUCT, unit="pcs", track_stock=True)
    make_item(db_session, org, name="Plain", type=ItemType.PRODUCT)  # does not track stock
    make_item(db_session, org, name="Massage")  # a service
    make_item(db_session, other, name="Theirs", type=ItemType.PRODUCT, track_stock=True)
    client.post(f"/api/items/{shelf.id}/stock", json={"kind": "count", "quantity": "7"}, headers=owner)

    rows = client.get("/api/inventory/items", headers=owner).json()
    in_stock = client.get("/api/inventory/items", params={"state": "in_stock"}, headers=owner).json()
    out = client.get("/api/inventory/items", params={"state": "out_of_stock"}, headers=owner).json()
    by_sku = client.get("/api/inventory/items", params={"q": "ho-1"}, headers=owner).json()

    assert [(r["name"], r["sku"], r["on_hand"], r["available"], r["states"]) for r in rows] == [
        ("Fly spray", None, "0.000", "0.000", ["out_of_stock"]),
        ("Hoof oil", "HO-1", "7.000", "7.000", []),
    ]
    assert [r["name"] for r in in_stock] == ["Hoof oil"] and [r["name"] for r in out] == ["Fly spray"]
    assert [r["item_id"] for r in by_sku] == [str(shelf.id)]
    assert [r["name"] for r in client.get("/api/inventory/items", headers=other_owner).json()] == ["Theirs"]
    assert empty.id
