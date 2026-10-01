"""Item behaviour inside a single organization. Cross-tenant isolation is covered by
test_tenant_isolation_contract.py; money precision by test_item_money.py."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import ItemType
from tests.factories import make_item

VALID = {
    "type": "service",
    "name": "Horse massage",
    "unit": "session",
    "price_ex_vat": "850.00",
    "vat_rate": "25.00",
}


def test_create_read_list_update_delete_roundtrip(client: TestClient, member):
    _, headers = member

    created = client.post(
        "/api/items", json={**VALID, "description": "  60 minutes  "}, headers=headers
    )
    assert created.status_code == 201
    body = created.json()
    assert body["type"] == "service"
    assert body["description"] == "60 minutes"  # trimmed
    assert body["unit"] == "session"
    assert body["price_ex_vat"] == "850.00"
    assert body["vat_rate"] == "25.00"
    assert body["active"] is True
    assert "organization_id" not in body
    assert "currency" not in body

    assert client.get(f"/api/items/{body['id']}", headers=headers).json() == body
    assert [i["id"] for i in client.get("/api/items", headers=headers).json()] == [body["id"]]

    updated = client.patch(
        f"/api/items/{body['id']}",
        json={"price_ex_vat": "900.50", "active": False},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["price_ex_vat"] == "900.50"
    assert updated.json()["active"] is False
    assert updated.json()["name"] == "Horse massage"  # untouched
    assert updated.json()["vat_rate"] == "25.00"  # untouched
    assert updated.json()["updated_at"] >= body["updated_at"]

    assert client.delete(f"/api/items/{body['id']}", headers=headers).status_code == 204
    assert client.get(f"/api/items/{body['id']}", headers=headers).status_code == 404


def test_services_and_products_use_the_same_model(client: TestClient, member):
    _, headers = member
    product = {**VALID, "type": "product", "name": "Hoof oil", "unit": "pcs", "vat_rate": "12"}

    assert client.post("/api/items", json=VALID, headers=headers).status_code == 201
    created = client.post("/api/items", json=product, headers=headers)

    assert created.status_code == 201
    assert created.json()["type"] == "product"
    assert created.json()["vat_rate"] == "12.00"


def test_description_is_optional_and_can_be_cleared(client: TestClient, member):
    _, headers = member
    created = client.post("/api/items", json={**VALID, "description": "x"}, headers=headers).json()
    assert client.post("/api/items", json=VALID, headers=headers).json()["description"] is None

    cleared = client.patch(f"/api/items/{created['id']}", json={"description": None}, headers=headers)

    assert cleared.status_code == 200 and cleared.json()["description"] is None


def test_list_is_ordered_by_name(client: TestClient, db_session: Session, member):
    org, headers = member
    for name in ("Charlie", "alice", "Bob"):
        make_item(db_session, org, name)

    names = [i["name"] for i in client.get("/api/items", headers=headers).json()]

    assert names == sorted(names, key=str.lower)


def test_list_filters(client: TestClient, db_session: Session, member):
    org, headers = member
    make_item(db_session, org, "Massage", ItemType.SERVICE, description="Relaxing treatment")
    make_item(db_session, org, "Hoof oil", ItemType.PRODUCT, unit="pcs")
    make_item(db_session, org, "Old service", ItemType.SERVICE, active=False)

    def names(**params):
        response = client.get("/api/items", params=params, headers=headers)
        assert response.status_code == 200
        return [i["name"] for i in response.json()]

    assert names(type="service") == ["Massage", "Old service"]
    assert names(type="product") == ["Hoof oil"]
    assert names(active="false") == ["Old service"]
    assert names(active="true", type="service") == ["Massage"]
    assert names(q="OIL") == ["Hoof oil"]
    assert names(q="relaxing") == ["Massage"]  # matches description too
    assert names(q="%") == []  # wildcards are literal
    assert client.get("/api/items", params={"type": "gadget"}, headers=headers).status_code == 422


@pytest.mark.parametrize(
    "override",
    [
        {"type": "gadget"},
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 256},
        {"unit": ""},
        {"unit": "  "},
        {"unit": "u" * 33},
        {"description": "d" * 2001},
        {"currency": "SEK"},  # no currency on Item
        {"organization_id": "00000000-0000-4000-8000-0000000000b2"},
        {"unexpected": 1},
        {"price_ex_vat": None},
        {"vat_rate": None},
    ],
)
def test_create_validation(client: TestClient, member, override):
    _, headers = member
    assert client.post("/api/items", json={**VALID, **override}, headers=headers).status_code == 422


@pytest.mark.parametrize("missing", ["type", "name", "unit", "price_ex_vat", "vat_rate"])
def test_create_requires_every_core_field(client: TestClient, member, missing):
    _, headers = member
    body = {k: v for k, v in VALID.items() if k != missing}
    assert client.post("/api/items", json=body, headers=headers).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"type": None},
        {"name": None},
        {"unit": None},
        {"price_ex_vat": None},
        {"vat_rate": None},
        {"active": None},
        {"name": ""},
        {"price_ex_vat": "-1.00"},
        {"vat_rate": "101"},
        {"type": "gadget"},
    ],
)
def test_update_validation(client: TestClient, db_session: Session, member, body):
    org, headers = member
    item = make_item(db_session, org)

    assert client.patch(f"/api/items/{item.id}", json=body, headers=headers).status_code == 422


def test_empty_patch_changes_nothing(client: TestClient, db_session: Session, member):
    org, headers = member
    item = make_item(db_session, org)
    before = client.get(f"/api/items/{item.id}", headers=headers).json()

    response = client.patch(f"/api/items/{item.id}", json={}, headers=headers)

    assert response.status_code == 200
    assert response.json() == before


def test_malformed_id_is_422_and_limit_is_bounded(client: TestClient, member):
    _, headers = member
    assert client.get("/api/items/not-a-uuid", headers=headers).status_code == 422
    assert client.get("/api/items", params={"limit": 201}, headers=headers).status_code == 422
    assert client.get("/api/items", params={"limit": 0}, headers=headers).status_code == 422
