"""Inventory I1: article numbers, stock tracking, the append-only stock ledger, and core lifecycle effects.

Every on-hand figure is explained by movements (who, when, why, before and after); stock never goes below zero; only
products track stock; the ledger is tenant-owned and never edited. Lifecycle effects run inside the step's database
transaction, after the validators, and a refusing effect leaves nothing of the step behind.
"""

import uuid
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, engine
from app.core.entity_registry import registry
from app.core.lifecycle import Problem
from app.main import app
from app.models import ItemType, Role
from app.modules.inventory.models import StockMovement
from app.modules.sales.models import Transaction
from tests.factories import add_member, make_customer, make_item, make_line, make_org, make_transaction, make_user

D = Decimal


def _world(db: Session, role: Role = Role.OWNER):
    org = make_org(db)
    user = make_user(db)
    add_member(db, org, user, role)
    return org, {"X-Dev-User-Email": user.email}


def _product(db: Session, org, **fields):
    return make_item(db, org, name="Liniment", type=ItemType.PRODUCT, unit="pcs", price_ex_vat="120.00", track_stock=True, **fields)


def _adjust(client: TestClient, item_id, headers, **body):
    return client.post(f"/api/items/{item_id}/stock", json=body, headers=headers)


# --- items: article number and stock tracking -----------------------------------------------------------------------


def test_an_item_gets_an_article_number_unique_within_its_organization_only(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    _, other_owner = _world(db_session)
    body = {"type": "product", "name": "Liniment", "unit": "pcs", "price_ex_vat": "120", "vat_rate": "25", "sku": " LIN-01 ", "track_stock": True}

    created = client.post("/api/items", json=body, headers=owner)
    duplicate = client.post("/api/items", json={**body, "name": "Other"}, headers=owner)
    elsewhere = client.post("/api/items", json=body, headers=other_owner)

    assert created.status_code == 201 and created.json()["sku"] == "LIN-01" and created.json()["track_stock"] is True
    assert duplicate.status_code == 422 and duplicate.json()["detail"][0]["type"] == "item.sku_taken"
    assert elsewhere.status_code == 201  # another organization's article numbers are none of this one's business
    cleared = client.patch(f"/api/items/{created.json()['id']}", json={"sku": None}, headers=owner)
    assert cleared.status_code == 200 and cleared.json()["sku"] is None


def test_only_a_product_tracks_stock(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    service = client.post(
        "/api/items", json={"type": "service", "name": "Massage", "unit": "h", "price_ex_vat": "850", "vat_rate": "25", "track_stock": True}, headers=owner
    )
    product = _product(db_session, org)

    assert service.status_code == 422
    # Turning a stock-tracking product into a service is refused too: the rule is about the item as it will be.
    turned = client.patch(f"/api/items/{product.id}", json={"type": "service"}, headers=owner)
    assert turned.status_code == 422 and turned.json()["detail"][0]["type"] == "item.track_stock_service"
    with pytest.raises(IntegrityError):  # and the database says the same
        make_item(db_session, org, name="Bad", type=ItemType.SERVICE, track_stock=True)


# --- the ledger -----------------------------------------------------------------------------------------------------


def test_the_first_count_is_the_opening_stock_and_later_changes_need_a_reason(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    item = _product(db_session, org)

    opening = _adjust(client, item.id, owner, kind="count", quantity="12")
    without_note = _adjust(client, item.id, owner, kind="remove", quantity="2")
    removed = _adjust(client, item.id, owner, kind="remove", quantity="2", note="Damaged in transport")
    counted = _adjust(client, item.id, owner, kind="count", quantity="9.5", note="Stock count")
    added = _adjust(client, item.id, owner, kind="add", quantity="0.5", note="Found in the van")

    assert opening.status_code == 201 and opening.json()["on_hand"] == "12.000"
    assert without_note.status_code == 422 and without_note.json()["detail"][0]["type"] == "stock.note_required"
    assert removed.json()["on_hand"] == "10.000" and counted.json()["on_hand"] == "9.500" and added.json()["on_hand"] == "10.000"
    movements = client.get(f"/api/items/{item.id}/stock", headers=owner).json()["movements"]
    assert [(m["reason"], m["quantity_before"], m["quantity_change"], m["quantity_after"]) for m in movements] == [
        ("adjustment", "9.500", "0.500", "10.000"),
        ("adjustment", "10.000", "-0.500", "9.500"),
        ("adjustment", "12.000", "-2.000", "10.000"),
        ("opening", "0.000", "12.000", "12.000"),
    ]
    assert {m["created_by_name"] for m in movements} != {None}


def test_stock_never_goes_below_zero_and_a_count_that_changes_nothing_is_refused(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    item = _product(db_session, org)
    _adjust(client, item.id, owner, kind="count", quantity="3")

    too_many = _adjust(client, item.id, owner, kind="remove", quantity="4", note="Lost")
    same = _adjust(client, item.id, owner, kind="count", quantity="3", note="Counted")

    assert too_many.status_code == 422 and too_many.json()["detail"][0]["type"] == "stock.negative"
    assert same.status_code == 422 and same.json()["detail"][0]["type"] == "stock.unchanged"
    assert client.get(f"/api/items/{item.id}/stock", headers=owner).json()["on_hand"] == "3.000"


def test_an_item_that_does_not_track_stock_has_no_ledger_entries(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    untracked = make_item(db_session, org, name="Plain product", type=ItemType.PRODUCT)
    service = make_item(db_session, org)

    for item in (untracked, service):
        response = _adjust(client, item.id, owner, kind="count", quantity="1")
        assert response.status_code == 422 and response.json()["detail"][0]["type"] == "stock.not_tracked"


def test_the_ledger_is_append_only_and_internally_consistent(db_session: Session):
    org = make_org(db_session)
    item = _product(db_session, org)
    db_session.add(StockMovement(organization_id=org.id, item_id=item.id, quantity_change=D("5"), quantity_before=D("0"), quantity_after=D("5"), reason="opening"))
    db_session.commit()

    for statement in ("UPDATE stock_movements SET note = 'x'", "DELETE FROM stock_movements"):
        with pytest.raises(DBAPIError, match="append-only"), db_session.begin_nested():
            db_session.execute(text(statement))
    # Wrong arithmetic, a negative "after", an adjustment without a note.
    for change, before, after, reason in [("5", "5", "11", "receipt"), ("-6", "5", "-1", "delivery"), ("1", "5", "6", "adjustment")]:
        with pytest.raises(IntegrityError), db_session.begin_nested():
            db_session.add(StockMovement(organization_id=org.id, item_id=item.id, quantity_change=D(change), quantity_before=D(before), quantity_after=D(after), reason=reason))
            db_session.flush()
    assert db_session.scalar(select(StockMovement.quantity_after).where(StockMovement.item_id == item.id)) == D("5")


def test_an_item_with_stock_movements_cannot_be_deleted(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    item = _product(db_session, org)
    _adjust(client, item.id, owner, kind="count", quantity="1")

    assert client.delete(f"/api/items/{item.id}", headers=owner).status_code == 409


# --- who may, and tenant isolation ----------------------------------------------------------------------------------


def test_a_viewer_reads_the_stock_but_cannot_change_it(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    viewer = make_user(db_session)
    add_member(db_session, org, viewer, Role.VIEWER)
    headers = {"X-Dev-User-Email": viewer.email}
    item = _product(db_session, org)
    _adjust(client, item.id, owner, kind="count", quantity="4")

    assert client.get(f"/api/items/{item.id}/stock", headers=headers).json()["on_hand"] == "4.000"
    assert _adjust(client, item.id, headers, kind="add", quantity="1", note="x").status_code == 403


def test_another_organizations_item_stock_is_not_found(client: TestClient, db_session: Session):
    org_a, owner_a = _world(db_session)
    org_b, owner_b = _world(db_session)
    item_b = _product(db_session, org_b)
    _adjust(client, item_b.id, owner_b, kind="count", quantity="7")

    assert client.get(f"/api/items/{item_b.id}/stock", headers=owner_a).status_code == 404
    assert _adjust(client, item_b.id, owner_a, kind="count", quantity="0", note="x").status_code == 404
    assert client.get(f"/api/items/{item_b.id}/stock", headers=owner_b).json()["on_hand"] == "7.000"


# --- core lifecycle effects -----------------------------------------------------------------------------------------


def _completable(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    tx = make_transaction(db_session, org, billing_customer=make_customer(db_session, org))
    make_line(db_session, org, tx)
    version = client.get(f"/api/transactions/{tx.id}", headers=owner).json()["version"]
    return org, owner, tx, version


def test_an_effect_runs_after_the_step_inside_its_database_transaction(client: TestClient, db_session: Session, monkeypatch):
    org, owner, tx, version = _completable(client, db_session)
    seen = []

    def effect(db, ctx, event, entity_key, entity_id):
        status_now = db.scalar(select(Transaction.status).where(Transaction.id == entity_id))
        seen.append((event, entity_key, entity_id, ctx.organization_id, status_now))

    monkeypatch.setattr(registry, "_effects", [effect])
    response = client.post(f"/api/transactions/{tx.id}/complete", headers={**owner, "If-Match": f'"{version}"'})

    assert response.status_code == 200
    assert seen == [("complete", "transaction", tx.id, org.id, "completed")]  # it sees the new status, uncommitted


def test_a_refusing_effect_rolls_the_whole_step_back(dev_auth, monkeypatch):
    # Real committed data and the application's own sessions: the rollback-only test session would hide the rollback.
    with SessionLocal() as db:
        org, user = make_org(db, f"Effect rollback {uuid.uuid4().hex[:8]}"), make_user(db)
        add_member(db, org, user, Role.OWNER)
        tx = make_transaction(db, org, billing_customer=make_customer(db, org))
        make_line(db, org, tx)
        db.commit()
        org_id, user_id, email, tx_id = org.id, user.id, user.email, tx.id
    try:
        def effect(db, ctx, event, entity_key, entity_id):
            raise HTTPException(409, detail="refused by an effect")

        monkeypatch.setattr(registry, "_effects", [effect])
        client = TestClient(app)
        headers = {"X-Dev-User-Email": email}
        version = client.get(f"/api/transactions/{tx_id}", headers=headers).json()["version"]

        response = client.post(f"/api/transactions/{tx_id}/complete", headers={**headers, "If-Match": f'"{version}"'})

        assert response.status_code == 409
        after = client.get(f"/api/transactions/{tx_id}", headers=headers).json()
        assert after["status"] == "draft" and after["version"] == version
        history = client.get("/api/history", params={"entity_type": "transaction", "entity_id": str(tx_id)}, headers=headers).json()["events"]
        assert all(event["action"] != "completed" for event in history)
    finally:
        with engine.begin() as conn:
            conn.execute(text("select set_config('app.deleting_organization', cast(:o as text), true)"), {"o": org_id})
            for table in ("audit_events", "transaction_lines", "transactions", "customers", "organization_users"):
                conn.execute(text(f"delete from {table} where organization_id = :o"), {"o": org_id})
            conn.execute(text("delete from organizations where id = :o"), {"o": org_id})
            conn.execute(text("delete from users where id = :u"), {"u": user_id})


def test_a_vetoed_step_never_reaches_an_effect(client: TestClient, db_session: Session, monkeypatch):
    _, owner, tx, version = _completable(client, db_session)
    reached = []
    veto = Problem(code="test.veto", message="Not now", entity_type="transaction", entity_id=str(tx.id))
    monkeypatch.setattr(registry, "_validators", [lambda db, ctx, event, key, entity_id: [veto]])
    monkeypatch.setattr(registry, "_effects", [lambda *args: reached.append(args)])

    response = client.post(f"/api/transactions/{tx.id}/complete", headers={**owner, "If-Match": f'"{version}"'})

    assert response.status_code == 409 and response.json()["detail"]["problems"][0]["code"] == "test.veto"
    assert reached == []


# --- I2: availability, a warning and never a refusal ----------------------------------------------------------------


def test_availability_lists_only_this_organizations_stock_tracking_items(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    other_org, other_owner = _world(db_session)
    tracked = _product(db_session, org)
    untracked = make_item(db_session, org, name="Plain product", type=ItemType.PRODUCT)
    foreign = _product(db_session, other_org)
    _adjust(client, tracked.id, owner, kind="count", quantity="5")
    _adjust(client, foreign.id, other_owner, kind="count", quantity="99")

    response = client.get("/api/inventory/availability", params={"item_id": [str(tracked.id), str(untracked.id), str(foreign.id)]}, headers=owner)

    assert response.json() == [{"item_id": str(tracked.id), "on_hand": "5.000", "committed": "0.000", "available": "5.000", "incoming": "0.000", "low_stock_threshold": None, "states": []}]


def test_a_draft_shows_the_shortage_over_all_its_lines_and_lines_are_never_refused_for_stock(client: TestClient, db_session: Session):
    org, owner = _world(db_session)
    item = _product(db_session, org)
    _adjust(client, item.id, owner, kind="count", quantity="5")
    tx = make_transaction(db_session, org, billing_customer=make_customer(db_session, org))
    make_line(db_session, org, tx, item=item, description="Liniment", unit="pcs", quantity="4", unit_price_ex_vat="120.00")

    added = client.post(f"/api/transactions/{tx.id}/lines", json={"item_id": str(item.id), "quantity": "4"}, headers=owner)

    assert added.status_code == 201  # 8 asked, 5 on hand: allowed; the shortage is only a warning
    demand = client.get(f"/api/inventory/transactions/{tx.id}", headers=owner).json()
    assert demand == [{"item_id": str(item.id), "requested": "8.000", "on_hand": "5.000", "available": "5.000", "incoming": "0.000", "shortage": "3.000"}]


def test_another_organizations_transaction_demand_is_not_found(client: TestClient, db_session: Session):
    _, owner_a = _world(db_session)
    org_b, _ = _world(db_session)
    tx_b = make_transaction(db_session, org_b, billing_customer=make_customer(db_session, org_b))

    assert client.get(f"/api/inventory/transactions/{tx_b.id}", headers=owner_a).status_code == 404
