"""Suppliers: a tenant-owned register like customers, chosen on incoming stock.

Record writers create, edit, deactivate and delete; everyone reads. A supplier named on a delivery cannot be deleted.
Incoming stock takes only an active supplier of the same organization; another organization's supplier (or a random
id) is "not found", whether read, changed, deleted or referenced.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import ItemType, Role
from tests.factories import add_member, make_item, make_org, make_user


def _member(db: Session, org, role: Role = Role.OWNER) -> dict[str, str]:
    user = make_user(db)
    add_member(db, org, user, role)
    return {"X-Dev-User-Email": user.email, "X-Organization-Id": str(org.id)}


def _create(client: TestClient, headers, **fields):
    return client.post("/api/suppliers", json={"name": "Horse Supplies AB", **fields}, headers=headers)


def _incoming(client: TestClient, headers, item, supplier_id):
    return client.post("/api/inventory/incoming", json={"item_id": str(item.id), "quantity": "5", "supplier_id": supplier_id}, headers=headers)


def test_a_supplier_is_created_read_listed_edited_and_kept_in_history(client: TestClient, db_session: Session):
    org = make_org(db_session)
    owner = _member(db_session, org)

    created = _create(client, owner, contact_person="Eva", email=" eva@supplies.test ", our_customer_number="K-114", country_code="se", city="Umeå")
    assert created.status_code == 201, created.text
    supplier = created.json()
    assert (supplier["name"], supplier["email"], supplier["country_code"], supplier["active"]) == ("Horse Supplies AB", "eva@supplies.test", "SE", True)
    assert supplier["created_by"] is not None

    edited = client.patch(f"/api/suppliers/{supplier['id']}", json={"phone": "090-123", "contact_person": ""}, headers=owner).json()
    assert (edited["phone"], edited["contact_person"]) == ("090-123", None)
    assert [row["name"] for row in client.get("/api/suppliers", params={"q": "supplies"}, headers=owner).json()] == ["Horse Supplies AB"]
    assert client.patch(f"/api/suppliers/{supplier['id']}", json={"name": None}, headers=owner).status_code == 422
    events = client.get("/api/history", params={"entity_type": "supplier", "entity_id": supplier["id"]}, headers=owner).json()["events"]
    assert {e["action"] for e in events} >= {"created", "updated"}


def test_incoming_stock_names_an_active_supplier_of_the_organization(client: TestClient, db_session: Session):
    org = make_org(db_session)
    owner = _member(db_session, org)
    item = make_item(db_session, org, name="Liniment", type=ItemType.PRODUCT, unit="pcs", price_ex_vat="120.00", track_stock=True)
    supplier = _create(client, owner).json()
    inactive = _create(client, owner, name="Old Supplier", active=False).json()

    created = _incoming(client, owner, item, supplier["id"])
    refused = _incoming(client, owner, item, inactive["id"])
    typed = client.post("/api/inventory/incoming", json={"item_id": str(item.id), "quantity": "5", "supplier": "Typed AB"}, headers=owner)

    assert created.status_code == 201 and created.json()["supplier"] == {"id": supplier["id"], "name": "Horse Supplies AB", "active": True}
    assert refused.status_code == 422 and refused.json()["detail"][0]["type"] == "reference.inactive"
    assert typed.status_code == 422  # a free-text supplier is no longer accepted
    def listed():  # one list read, by id: deliveries made in one test share a timestamp, so their order is not fixed
        return {row["id"]: row for row in client.get("/api/inventory/incoming", headers=owner).json()}

    assert listed()[created.json()["id"]]["supplier"]["name"] == "Horse Supplies AB"
    of_supplier = client.get("/api/inventory/incoming", params={"supplier_id": supplier["id"], "open_only": "false"}, headers=owner).json()
    assert [row["id"] for row in of_supplier] == [created.json()["id"]]
    assert _incoming(client, owner, item, None).json()["supplier"] is None  # still optional

    blocked = client.delete(f"/api/suppliers/{supplier['id']}", headers=owner)
    assert blocked.status_code == 409
    client.patch(f"/api/suppliers/{supplier['id']}", json={"active": False}, headers=owner)
    assert listed()[created.json()["id"]]["supplier"]["active"] is False  # the delivery keeps it
    assert client.delete(f"/api/suppliers/{inactive['id']}", headers=owner).status_code == 204


@pytest.mark.parametrize("role,allowed", [(Role.ADMIN, True), (Role.EMPLOYEE, True), (Role.VIEWER, False)])
def test_who_may_change_suppliers(client: TestClient, db_session: Session, role, allowed):
    org = make_org(db_session)
    headers = _member(db_session, org, role)
    assert _create(client, headers).status_code == (201 if allowed else 403)
    assert client.get("/api/suppliers", headers=headers).status_code == 200


def test_another_organizations_supplier_is_out_of_reach(client: TestClient, db_session: Session):
    org_a, org_b = make_org(db_session, "Org A"), make_org(db_session, "Org B")
    a, b = _member(db_session, org_a), _member(db_session, org_b)
    a_supplier = _create(client, a).json()
    b_supplier = _create(client, b).json()  # same name in both
    item = make_item(db_session, org_a, name="Liniment", type=ItemType.PRODUCT, unit="pcs", price_ex_vat="1.00", track_stock=True)

    assert client.get(f"/api/suppliers/{b_supplier['id']}", headers=a).status_code == 404
    assert client.patch(f"/api/suppliers/{b_supplier['id']}", json={"name": "Taken"}, headers=a).status_code == 404
    assert client.delete(f"/api/suppliers/{b_supplier['id']}", headers=a).status_code == 404
    assert [row["id"] for row in client.get("/api/suppliers", params={"q": "Horse"}, headers=a).json()] == [a_supplier["id"]]
    reference = _incoming(client, a, item, b_supplier["id"])
    assert reference.status_code == 422 and reference.json()["detail"][0]["type"] == "reference.not_found"
    assert client.get(f"/api/suppliers/{b_supplier['id']}", headers=b).json()["name"] == "Horse Supplies AB"
