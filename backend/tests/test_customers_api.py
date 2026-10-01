"""Customer CRUD behaviour inside a single organization (isolation lives in
test_customers_isolation.py)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Customer, Role
from tests.factories import add_member, make_customer, make_org, make_user


@pytest.fixture
def member(db_session: Session):
    org, user = make_org(db_session, "Solo"), make_user(db_session)
    add_member(db_session, org, user, Role.OWNER)
    return org, {"X-Dev-User-Email": user.email}


def test_create_read_list_update_delete_roundtrip(client: TestClient, member):
    _, headers = member

    created = client.post(
        "/api/customers",
        json={"customer_type": "person", "name": "  Anna Andersson ", "email": "anna@example.test"},
        headers=headers,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Anna Andersson"  # trimmed
    assert body["active"] is True
    assert body["phone"] is None
    assert "organization_id" not in body

    assert client.get(f"/api/customers/{body['id']}", headers=headers).json() == body
    assert [c["id"] for c in client.get("/api/customers", headers=headers).json()] == [body["id"]]

    updated = client.patch(
        f"/api/customers/{body['id']}",
        json={"phone": "070-123", "active": False},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["phone"] == "070-123"
    assert updated.json()["active"] is False
    assert updated.json()["name"] == "Anna Andersson"  # untouched field kept
    assert updated.json()["updated_at"] >= body["updated_at"]

    assert client.delete(f"/api/customers/{body['id']}", headers=headers).status_code == 204
    assert client.get(f"/api/customers/{body['id']}", headers=headers).status_code == 404
    assert client.get("/api/customers", headers=headers).json() == []


def test_list_is_ordered_by_name(client: TestClient, db_session: Session, member):
    org, headers = member
    for name in ("Charlie", "alice", "Bob"):
        make_customer(db_session, org, name, email=None)

    names = [c["name"] for c in client.get("/api/customers", headers=headers).json()]

    assert names == sorted(names, key=str.lower)


def test_search_matches_name_or_email_case_insensitively(
    client: TestClient, db_session: Session, member
):
    org, headers = member
    make_customer(db_session, org, "Anna Andersson", email="a@example.test")
    make_customer(db_session, org, "Umeå HK", email="stable@umea.test")

    by_name = client.get("/api/customers", params={"q": "ANNA"}, headers=headers).json()
    by_email = client.get("/api/customers", params={"q": "UMEA.test"}, headers=headers).json()

    assert [c["name"] for c in by_name] == ["Anna Andersson"]
    assert [c["name"] for c in by_email] == ["Umeå HK"]


def test_search_treats_percent_literally(client: TestClient, db_session: Session, member):
    org, headers = member
    make_customer(db_session, org, "100% Horse", email=None)
    make_customer(db_session, org, "Other", email=None)

    found = client.get("/api/customers", params={"q": "%"}, headers=headers).json()

    assert [c["name"] for c in found] == ["100% Horse"]


def test_same_name_and_email_are_allowed_within_an_organization(
    client: TestClient, member
):
    _, headers = member
    body = {"customer_type": "person", "name": "Twin", "email": "t@example.test"}

    assert client.post("/api/customers", json=body, headers=headers).status_code == 201
    assert client.post("/api/customers", json=body, headers=headers).status_code == 201


@pytest.mark.parametrize(
    "body",
    [
        {"name": "No type"},
        {"customer_type": "organization", "name": "Wrong type"},  # "organization" means tenant
        {"customer_type": "person", "name": ""},
        {"customer_type": "person", "name": "   "},
        {"customer_type": "person", "name": "x" * 256},
        {"customer_type": "person", "name": "ok", "unexpected": 1},
    ],
)
def test_create_validation(client: TestClient, member, body):
    _, headers = member
    assert client.post("/api/customers", json=body, headers=headers).status_code == 422


@pytest.mark.parametrize(
    "body",
    [{"name": None}, {"customer_type": None}, {"active": None}, {"name": ""}, {"customer_type": "x"}],
)
def test_update_validation(client: TestClient, db_session: Session, member, body):
    org, headers = member
    customer = make_customer(db_session, org)

    response = client.patch(f"/api/customers/{customer.id}", json=body, headers=headers)

    assert response.status_code == 422


def test_update_can_clear_optional_fields(client: TestClient, db_session: Session, member):
    org, headers = member
    customer = make_customer(db_session, org)

    response = client.patch(
        f"/api/customers/{customer.id}", json={"email": None, "phone": None}, headers=headers
    )

    assert response.status_code == 200
    assert response.json()["email"] is None and response.json()["phone"] is None


def test_malformed_customer_id_is_422(client: TestClient, member):
    _, headers = member
    assert client.get("/api/customers/not-a-uuid", headers=headers).status_code == 422


def test_list_limit_is_bounded(client: TestClient, member):
    _, headers = member
    assert client.get("/api/customers", params={"limit": 201}, headers=headers).status_code == 422
    assert client.get("/api/customers", params={"limit": 0}, headers=headers).status_code == 422


def test_database_requires_an_organization(db_session: Session):
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(Customer(customer_type="person", name="Orphan"))
        db_session.flush()


def test_database_rejects_unknown_organization(db_session: Session):
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(
            Customer(organization_id=uuid.uuid4(), customer_type="person", name="Ghost")
        )
        db_session.flush()


def test_database_rejects_unknown_customer_type(db_session: Session):
    org = make_org(db_session)
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(Customer(organization_id=org.id, customer_type="alien", name="x"))
        db_session.flush()
