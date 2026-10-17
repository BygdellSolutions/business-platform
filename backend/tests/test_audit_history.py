"""Who changed what, when, and what it was before: the history of business records.

Every write records an event in the same database transaction; a write that changes nothing records nothing; a
refused write leaves no event; one organization never sees another's history; history cannot be edited.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models import AuditEvent, Role
from tests.factories import add_member, make_customer, make_definition, make_item, make_org, make_transaction, make_user
from tests.test_migration_currency import _columns, _execute, _scalar, _upgrade, scratch_url  # noqa: F401

HISTORY = "/api/history"


def _team(db: Session, name: str = "History Org") -> SimpleNamespace:
    org = make_org(db, name)
    owner = make_user(db, name="Olle Owner")
    viewer = make_user(db, name="Vera Viewer")
    add_member(db, org, owner, Role.OWNER)
    add_member(db, org, viewer, Role.VIEWER)
    return SimpleNamespace(org=org, owner=owner, h={"X-Dev-User-Email": owner.email}, viewer_h={"X-Dev-User-Email": viewer.email})


def _history(client: TestClient, headers, entity_type: str, entity_id) -> dict:
    response = client.get(HISTORY, params={"entity_type": entity_type, "entity_id": str(entity_id)}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _event_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(AuditEvent))


def test_a_customer_records_who_created_and_changed_it_and_the_old_values(client: TestClient, db_session: Session):
    t = _team(db_session)
    created = client.post("/api/customers", json={"name": "Anna", "customer_type": "person", "email": "a@example.test"}, headers=t.h).json()
    assert created["created_by"] == str(t.owner.id) and created["updated_by"] == str(t.owner.id)

    client.patch(f"/api/customers/{created['id']}", json={"name": "Anna Andersson", "email": "anna@example.test"}, headers=t.h)

    history = _history(client, t.viewer_h, "customer", created["id"])  # any member may read it
    assert [e["action"] for e in history["events"]] == ["updated", "created"]  # newest first
    update, creation = history["events"]
    assert update["changes"] == {
        "email": {"from": "a@example.test", "to": "anna@example.test"},
        "name": {"from": "Anna", "to": "Anna Andersson"},
    }
    assert creation["changes"]["name"] == {"from": None, "to": "Anna"}
    assert update["actor"] == {"id": str(t.owner.id), "name": "Olle Owner"}
    assert history["people"] == [{"id": str(t.owner.id), "name": "Olle Owner"}]


def test_a_write_that_changes_nothing_records_nothing(client: TestClient, db_session: Session):
    t = _team(db_session)
    customer = client.post("/api/customers", json={"name": "Same", "customer_type": "person"}, headers=t.h).json()
    before = _event_count(db_session)

    assert client.patch(f"/api/customers/{customer['id']}", json={"name": "Same"}, headers=t.h).status_code == 200

    assert _event_count(db_session) == before


def test_deactivation_and_deletion_are_recorded_with_what_was_there(client: TestClient, db_session: Session):
    t = _team(db_session)
    item = client.post("/api/items", json={"type": "service", "name": "Massage", "unit": "st", "price_ex_vat": "850.00", "vat_rate": "25.00"}, headers=t.h).json()

    client.patch(f"/api/items/{item['id']}", json={"active": False, "price_ex_vat": "900"}, headers=t.h)
    client.delete(f"/api/items/{item['id']}", headers=t.h)

    events = _history(client, t.h, "item", item["id"])["events"]
    assert [e["action"] for e in events] == ["deleted", "updated", "created"]
    assert events[1]["changes"] == {"active": {"from": True, "to": False}, "price_ex_vat": {"from": "850.00", "to": "900.00"}}
    assert events[0]["changes"]["name"] == {"from": "Massage", "to": None}


def test_a_refused_delete_leaves_no_event(client: TestClient, db_session: Session):
    t = _team(db_session)
    customer = make_customer(db_session, t.org)
    make_transaction(db_session, t.org, billing_customer=customer)  # references the customer
    before = _event_count(db_session)

    assert client.delete(f"/api/customers/{customer.id}", headers=t.h).status_code == 409

    assert _event_count(db_session) == before


def test_a_horse_change_names_the_new_owner(client: TestClient, db_session: Session):
    t = _team(db_session)
    anna, umea = make_customer(db_session, t.org, "Anna"), make_customer(db_session, t.org, "Umeå HK")
    horse = client.post("/api/horses", json={"name": "Kalle", "owner_customer_id": str(anna.id)}, headers=t.h).json()

    client.patch(f"/api/horses/{horse['id']}", json={"owner_customer_id": str(umea.id)}, headers=t.h)

    update = _history(client, t.h, "horse", horse["id"])["events"][0]
    assert update["changes"] == {"owner_customer_id": {"from": str(anna.id), "to": str(umea.id)}}


def test_a_transaction_history_includes_its_lines_and_lifecycle(client: TestClient, db_session: Session):
    t = _team(db_session)
    customer = make_customer(db_session, t.org)
    item = make_item(db_session, t.org)
    tx = client.post("/api/transactions", json={"billing_customer_id": str(customer.id)}, headers=t.h).json()
    line = client.post(f"/api/transactions/{tx['id']}/lines", json={"item_id": str(item.id), "quantity": "1"}, headers=t.h).json()
    client.patch(f"/api/transactions/{tx['id']}/lines/{line['id']}", json={"quantity": "2"}, headers={**t.h, "If-Match": '"1"'})
    current = client.get(f"/api/transactions/{tx['id']}", headers=t.h).json()
    client.post(f"/api/transactions/{tx['id']}/complete", headers={**t.h, "If-Match": f'"{current["version"]}"'})

    events = _history(client, t.h, "transaction", tx["id"])["events"]
    assert [(e["entity_type"], e["action"]) for e in events] == [
        ("transaction", "completed"),
        ("transaction_line", "updated"),
        ("transaction_line", "created"),
        ("transaction", "created"),
    ]
    assert events[0]["changes"] == {"status": {"from": "draft", "to": "completed"}}
    assert events[1]["changes"]["quantity"] == {"from": "1.000", "to": "2.000"}
    final = client.get(f"/api/transactions/{tx['id']}", headers=t.h).json()
    assert final["created_by"] == final["updated_by"] == str(t.owner.id)


def test_an_invoice_records_its_creation_and_issuance(client: TestClient, db_session: Session):
    t = _team(db_session)
    tx = make_transaction(db_session, t.org, status="completed")
    invoice = client.post("/api/invoices", json={"transaction_ids": [str(tx.id)]}, headers=t.h).json()
    client.post(f"/api/invoices/{invoice['id']}/issue", headers={**t.h, "If-Match": f'"{invoice["version"]}"'})

    events = _history(client, t.h, "invoice", invoice["id"])["events"]
    assert [e["action"] for e in events] == ["issued", "created"]
    assert events[0]["changes"]["status"] == {"from": "draft", "to": "issued"}
    assert events[0]["changes"]["number_text"] == {"from": None, "to": "1001"}
    assert "customer_snapshot" not in events[1]["changes"]  # the frozen documents are not history
    read = client.get(f"/api/invoices/{invoice['id']}", headers=t.h).json()
    assert read["created_by"] == read["issued_by"] == str(t.owner.id)


def test_custom_field_changes_are_recorded_in_the_words_a_person_saw(client: TestClient, db_session: Session):
    t = _team(db_session)
    tx = make_transaction(db_session, t.org, lines=[])
    make_definition(db_session, t.org, entity_type="transaction", key="memo", label="Memo")
    url = f"/api/custom-fields/entities/transaction/{tx.id}/values"
    client.patch(url, json={"values": {"memo": "first"}}, headers=t.h)
    client.patch(url, json={"values": {"memo": "second"}}, headers=t.h)

    events = _history(client, t.h, "transaction", tx.id)["events"]
    assert events[0]["action"] == "fields_updated"
    assert events[0]["changes"] == {"memo": {"label": "Memo", "from": "first", "to": "second"}}
    assert events[1]["changes"] == {"memo": {"label": "Memo", "from": None, "to": "first"}}


def test_another_organizations_history_is_invisible(client: TestClient, db_session: Session):
    a, b = _team(db_session, "A"), _team(db_session, "B")
    in_b = client.post("/api/customers", json={"name": "Anna", "customer_type": "person"}, headers=b.h).json()

    assert _history(client, a.h, "customer", in_b["id"]) == {"events": [], "people": []}
    assert len(_history(client, b.h, "customer", in_b["id"])["events"]) == 1  # control: the history exists


def test_history_is_append_only(client: TestClient, db_session: Session):
    t = _team(db_session)
    client.post("/api/customers", json={"name": "Anna", "customer_type": "person"}, headers=t.h)
    for statement in ("UPDATE audit_events SET action = 'x'", "DELETE FROM audit_events"):
        with pytest.raises(DBAPIError, match="append-only"):
            with db_session.begin_nested():
                db_session.execute(text(statement + " WHERE organization_id = :o"), {"o": t.org.id})


def test_history_goes_only_with_its_own_organization(client: TestClient, db_session: Session):
    a, b = _team(db_session, "A"), _team(db_session, "B")
    client.post("/api/customers", json={"name": "Anna", "customer_type": "person"}, headers=a.h)
    client.post("/api/customers", json={"name": "Anna", "customer_type": "person"}, headers=b.h)

    with db_session.begin_nested():
        db_session.execute(text("SELECT set_config('app.deleting_organization', :o, true)"), {"o": str(a.org.id)})
        db_session.execute(text("DELETE FROM audit_events WHERE organization_id = :o"), {"o": a.org.id})
        with pytest.raises(DBAPIError, match="append-only"):
            with db_session.begin_nested():
                db_session.execute(text("DELETE FROM audit_events WHERE organization_id = :o"), {"o": b.org.id})


BEFORE = "a1c3e5f7b902"


def test_the_migration_records_no_author_for_existing_records(scratch_url: str):  # noqa: F811
    _upgrade(scratch_url, BEFORE)
    _execute(scratch_url, "INSERT INTO organizations (id, name) VALUES ('00000000-0000-4000-8000-000000000001', 'Old')")
    _execute(scratch_url, "INSERT INTO customers (organization_id, name, customer_type) VALUES ('00000000-0000-4000-8000-000000000001', 'Old customer', 'person')")

    _upgrade(scratch_url, "head")

    assert {"created_by", "updated_by"} <= _columns(scratch_url, "customers")
    assert _scalar(scratch_url, "SELECT count(*) FROM customers WHERE created_by IS NULL AND updated_by IS NULL") == 1
    assert _scalar(scratch_url, "SELECT count(*) FROM audit_events") == 0
