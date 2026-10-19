"""Inventory I5: the backorder backlog and its allocation.

The backlog is served oldest first. The system only PROPOSES how the stock on hand is shared; a person confirms it
(and may change it). Each confirmed allocation is a delivery movement for that sale (who and when), and nothing is
delivered beyond what a backorder still waits for or what is on hand.
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


def _product(db: Session, org, name="Liniment"):
    return make_item(db, org, name=name, type=ItemType.PRODUCT, unit="pcs", price_ex_vat="120.00", track_stock=True)


def _backordered_sale(client: TestClient, db: Session, org, headers, item, quantity: str, customer: str):
    tx = make_transaction(db, org, billing_customer=make_customer(db, org, customer))
    make_line(db, org, tx, item=item, description=item.name, unit="pcs", quantity=quantity, unit_price_ex_vat="120.00")
    assert client.post(f"/api/transactions/{tx.id}/complete", headers=headers).status_code == 200
    return tx


def _receive(client: TestClient, item, headers, quantity: str):
    incoming = client.post("/api/inventory/incoming", json={"item_id": str(item.id), "quantity": quantity}, headers=headers).json()
    assert client.post(f"/api/inventory/incoming/{incoming['id']}/receive", json={}, headers=headers).status_code == 200


def test_the_backlog_lists_waiting_sales_oldest_first(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    item = _product(db_session, org)
    first = _backordered_sale(client, db_session, org, owner, item, "2", "Anna Andersson")
    second = _backordered_sale(client, db_session, org, owner, item, "3", "Umeå HK")

    backlog = client.get("/api/inventory/backorders", headers=owner).json()

    assert [(b["transaction_id"], b["customer_name"], b["remaining"], b["state"]) for b in backlog] == [
        (str(first.id), "Anna Andersson", "2.000", "waiting_for_stock"),
        (str(second.id), "Umeå HK", "3.000", "waiting_for_stock"),
    ]


def test_the_proposal_shares_stock_oldest_first_and_changes_nothing(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    item = _product(db_session, org)
    first = _backordered_sale(client, db_session, org, owner, item, "2", "Anna Andersson")
    second = _backordered_sale(client, db_session, org, owner, item, "3", "Umeå HK")
    _receive(client, item, owner, "4")

    proposal = client.get(f"/api/inventory/items/{item.id}/allocation", headers=owner).json()

    assert proposal["on_hand"] == "4.000"
    assert [(p["transaction_id"], p["remaining"], p["proposed"]) for p in proposal["proposals"]] == [
        (str(first.id), "2.000", "2.000"),
        (str(second.id), "3.000", "2.000"),
    ]
    assert client.get(f"/api/items/{item.id}/stock", headers=owner).json()["on_hand"] == "4.000"  # nothing delivered yet


def test_a_confirmed_allocation_delivers_and_records_who_and_when(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    item = _product(db_session, org)
    first = _backordered_sale(client, db_session, org, owner, item, "2", "Anna Andersson")
    second = _backordered_sale(client, db_session, org, owner, item, "3", "Umeå HK")
    _receive(client, item, owner, "4")
    ids = [b["fulfillment_id"] for b in client.get("/api/inventory/backorders", headers=owner).json()]

    # The person changes the proposal: the newer sale gets 3, the older one 1.
    confirmed = client.post(
        f"/api/inventory/items/{item.id}/allocation",
        json={"allocations": [{"fulfillment_id": ids[0], "quantity": "1"}, {"fulfillment_id": ids[1], "quantity": "3"}]},
        headers=owner,
    )

    assert confirmed.status_code == 200
    states = {b["transaction_id"]: (b["fulfilled_later"], b["remaining"], b["state"]) for b in confirmed.json()}
    assert states == {str(first.id): ("1.000", "1.000", "partially_fulfilled"), str(second.id): ("3.000", "0.000", "fulfilled")}
    movements = client.get(f"/api/items/{item.id}/stock", headers=owner).json()["movements"]
    assert [(m["reason"], m["quantity_change"], m["transaction_id"], m["note"]) for m in movements[:2]] == [
        ("delivery", "-3.000", str(second.id), "Backorder fulfilled"),
        ("delivery", "-1.000", str(first.id), "Backorder fulfilled"),
    ]
    assert all(m["created_by_name"] for m in movements[:2])
    lines = client.get(f"/api/inventory/transactions/{first.id}/fulfillment", headers=owner).json()
    assert (lines[0]["delivered"], lines[0]["fulfilled_later"]) == ("0.000", "1.000")


def test_an_allocation_never_exceeds_a_backorder_or_the_stock_and_is_refused_whole(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    item = _product(db_session, org)
    _backordered_sale(client, db_session, org, owner, item, "2", "Anna Andersson")
    _backordered_sale(client, db_session, org, owner, item, "3", "Umeå HK")
    _receive(client, item, owner, "2")
    ids = [b["fulfillment_id"] for b in client.get("/api/inventory/backorders", headers=owner).json()]
    allocate = lambda allocations: client.post(f"/api/inventory/items/{item.id}/allocation", json={"allocations": allocations}, headers=owner)  # noqa: E731

    too_much = allocate([{"fulfillment_id": ids[0], "quantity": "3"}])
    beyond_stock = allocate([{"fulfillment_id": ids[0], "quantity": "1"}, {"fulfillment_id": ids[1], "quantity": "2"}])
    twice = allocate([{"fulfillment_id": ids[0], "quantity": "1"}, {"fulfillment_id": ids[0], "quantity": "1"}])

    assert too_much.status_code == 422 and too_much.json()["detail"][0]["type"] == "backorder.too_much"
    assert beyond_stock.status_code == 422 and beyond_stock.json()["detail"][0]["type"] == "allocation.beyond_stock"
    assert twice.status_code == 422
    assert client.get(f"/api/items/{item.id}/stock", headers=owner).json()["on_hand"] == "2.000"  # nothing of the refused ones happened
    assert [b["fulfilled_later"] for b in client.get("/api/inventory/backorders", headers=owner).json()] == ["0.000", "0.000"]


def test_a_backorder_of_another_item_or_organization_cannot_be_allocated(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    other_org, other_owner = _world(db_session)
    item, other_item = _product(db_session, org), _product(db_session, org, "Hoof oil")
    foreign_item = _product(db_session, other_org)
    _backordered_sale(client, db_session, org, owner, other_item, "1", "Anna Andersson")
    _backordered_sale(client, db_session, other_org, other_owner, foreign_item, "1", "Anna Andersson")
    _receive(client, item, owner, "5")
    other_items_backorder = client.get("/api/inventory/backorders", params={"item_id": str(other_item.id)}, headers=owner).json()[0]["fulfillment_id"]
    foreign_backorder = client.get("/api/inventory/backorders", headers=other_owner).json()[0]["fulfillment_id"]

    for fulfillment_id in (other_items_backorder, foreign_backorder):
        response = client.post(f"/api/inventory/items/{item.id}/allocation", json={"allocations": [{"fulfillment_id": fulfillment_id, "quantity": "1"}]}, headers=owner)
        assert response.status_code == 422 and response.json()["detail"][0]["type"] == "backorder.not_open"
    assert client.get(f"/api/inventory/items/{foreign_item.id}/allocation", headers=owner).status_code == 404
    assert len(client.get("/api/inventory/backorders", headers=owner).json()) == 1  # only its own


def test_a_viewer_reads_the_backlog_but_cannot_allocate(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    viewer = make_user(db_session)
    add_member(db_session, org, viewer, Role.VIEWER)
    headers = {"X-Dev-User-Email": viewer.email}
    item = _product(db_session, org)
    _backordered_sale(client, db_session, org, owner, item, "1", "Anna Andersson")
    _receive(client, item, owner, "1")
    [backorder] = client.get("/api/inventory/backorders", headers=headers).json()

    response = client.post(f"/api/inventory/items/{item.id}/allocation", json={"allocations": [{"fulfillment_id": backorder["fulfillment_id"], "quantity": "1"}]}, headers=headers)
    assert response.status_code == 403
