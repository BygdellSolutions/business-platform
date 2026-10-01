"""Cross-tenant isolation for Customers (CLAUDE.md sections 6 and 23).

Layout mirrors the dev seed:

    shared  - owner of A, admin of B   (multi-organization user)
    a_only  - member of A only
    b_only  - member of B only

Both organizations have an "Anna Andersson" with identical email and phone,
so any query that forgets the organization filter returns the wrong record or
two records, instead of failing loudly.
"""

import uuid
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Customer, CustomerType, Organization, Role, User
from tests.factories import add_member, make_customer, make_org, make_user


@dataclass
class Tenants:
    org_a: Organization
    org_b: Organization
    shared: User
    a_only: User
    b_only: User
    a_anna: Customer
    b_anna: Customer
    a_company: Customer
    b_zelda: Customer


@pytest.fixture
def tenants(db_session: Session) -> Tenants:
    org_a = make_org(db_session, "Org A")
    org_b = make_org(db_session, "Org B")
    shared, a_only, b_only = (make_user(db_session) for _ in range(3))
    add_member(db_session, org_a, shared, Role.OWNER)
    add_member(db_session, org_b, shared, Role.ADMIN)
    add_member(db_session, org_a, a_only, Role.EMPLOYEE)
    add_member(db_session, org_b, b_only, Role.EMPLOYEE)
    return Tenants(
        org_a=org_a,
        org_b=org_b,
        shared=shared,
        a_only=a_only,
        b_only=b_only,
        a_anna=make_customer(db_session, org_a),  # identical-looking pair
        b_anna=make_customer(db_session, org_b),
        a_company=make_customer(
            db_session, org_a, "Umeå HK", CustomerType.COMPANY, "hk@example.test", None
        ),
        b_zelda=make_customer(db_session, org_b, "Zelda Stable", email="zelda@example.test"),
    )


def h(user: User, org: Organization | None = None) -> dict[str, str]:
    headers = {"X-Dev-User-Email": user.email}
    if org is not None:
        headers["X-Organization-Id"] = str(org.id)
    return headers


def ids(response) -> set[str]:
    return {c["id"] for c in response.json()}


def customer_exists(db: Session, customer_id: uuid.UUID) -> bool:
    return db.scalar(select(func.count()).select_from(Customer).where(Customer.id == customer_id)) == 1


# --- list and search ---------------------------------------------------------------


def test_each_organization_lists_only_its_own_customers(client: TestClient, tenants: Tenants):
    a = client.get("/api/customers", headers=h(tenants.a_only))
    b = client.get("/api/customers", headers=h(tenants.b_only))

    assert a.status_code == b.status_code == 200
    assert ids(a) == {str(tenants.a_anna.id), str(tenants.a_company.id)}
    assert ids(b) == {str(tenants.b_anna.id), str(tenants.b_zelda.id)}


def test_shared_user_sees_only_the_selected_organizations_customers(
    client: TestClient, tenants: Tenants
):
    in_a = client.get("/api/customers", headers=h(tenants.shared, tenants.org_a))
    in_b = client.get("/api/customers", headers=h(tenants.shared, tenants.org_b))

    assert ids(in_a) == {str(tenants.a_anna.id), str(tenants.a_company.id)}
    assert ids(in_b) == {str(tenants.b_anna.id), str(tenants.b_zelda.id)}


def test_search_returns_only_the_active_organizations_matches(client: TestClient, tenants: Tenants):
    # "anna" matches one customer in each tenant; each side must see only its own.
    a = client.get("/api/customers", params={"q": "anna"}, headers=h(tenants.a_only))
    b = client.get("/api/customers", params={"q": "anna"}, headers=h(tenants.b_only))

    assert ids(a) == {str(tenants.a_anna.id)}
    assert ids(b) == {str(tenants.b_anna.id)}


def test_search_cannot_discover_another_organizations_customer(
    client: TestClient, tenants: Tenants
):
    # "Zelda" exists only in B. Searching by name or by email from A finds nothing.
    by_name = client.get("/api/customers", params={"q": "zelda"}, headers=h(tenants.a_only))
    by_email = client.get(
        "/api/customers", params={"q": "zelda@example.test"}, headers=h(tenants.a_only)
    )

    assert by_name.status_code == by_email.status_code == 200
    assert by_name.json() == [] and by_email.json() == []


def test_search_wildcards_are_literal_and_do_not_escape_the_tenant(
    client: TestClient, tenants: Tenants
):
    for q in ("%", "_", "%%"):
        response = client.get("/api/customers", params={"q": q}, headers=h(tenants.a_only))
        assert response.status_code == 200
        assert response.json() == []


def test_pagination_stays_inside_the_tenant(client: TestClient, tenants: Tenants):
    page = client.get(
        "/api/customers", params={"limit": 200, "offset": 0}, headers=h(tenants.a_only)
    )
    beyond = client.get("/api/customers", params={"offset": 2}, headers=h(tenants.a_only))

    assert len(page.json()) == 2
    assert beyond.json() == []  # does not spill into B's rows


# --- retrieve ------------------------------------------------------------------------


def test_retrieve_own_customer(client: TestClient, tenants: Tenants):
    response = client.get(f"/api/customers/{tenants.a_anna.id}", headers=h(tenants.a_only))

    assert response.status_code == 200
    assert response.json()["id"] == str(tenants.a_anna.id)


def test_cannot_retrieve_foreign_customer_even_with_known_uuid(
    client: TestClient, tenants: Tenants
):
    response = client.get(f"/api/customers/{tenants.b_anna.id}", headers=h(tenants.a_only))

    assert response.status_code == 404
    assert "Anna" not in response.text and str(tenants.b_anna.id) not in response.text


def test_foreign_uuid_is_indistinguishable_from_nonexistent_uuid(
    client: TestClient, tenants: Tenants
):
    foreign = client.get(f"/api/customers/{tenants.b_anna.id}", headers=h(tenants.a_only))
    missing = client.get(f"/api/customers/{uuid.uuid4()}", headers=h(tenants.a_only))

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    assert foreign.headers["content-length"] == missing.headers["content-length"]


def test_shared_user_cannot_read_the_other_organizations_customer_by_uuid(
    client: TestClient, tenants: Tenants
):
    # Same human, both memberships: the *active* organization is the boundary.
    own = client.get(f"/api/customers/{tenants.a_anna.id}", headers=h(tenants.shared, tenants.org_a))
    cross = client.get(
        f"/api/customers/{tenants.b_anna.id}", headers=h(tenants.shared, tenants.org_a)
    )
    after_switch = client.get(
        f"/api/customers/{tenants.b_anna.id}", headers=h(tenants.shared, tenants.org_b)
    )

    assert own.status_code == 200
    assert cross.status_code == 404
    assert after_switch.status_code == 200


# --- update --------------------------------------------------------------------------


def test_cannot_update_foreign_customer(client: TestClient, db_session: Session, tenants: Tenants):
    response = client.patch(
        f"/api/customers/{tenants.b_anna.id}",
        json={"name": "Hijacked", "email": "evil@example.test"},
        headers=h(tenants.a_only),
    )

    assert response.status_code == 404
    db_session.refresh(tenants.b_anna)
    assert tenants.b_anna.name == "Anna Andersson"
    assert tenants.b_anna.email == "anna@example.test"


def test_update_of_foreign_uuid_looks_like_update_of_nonexistent_uuid(
    client: TestClient, tenants: Tenants
):
    body = {"name": "x"}
    foreign = client.patch(f"/api/customers/{tenants.b_anna.id}", json=body, headers=h(tenants.a_only))
    missing = client.patch(f"/api/customers/{uuid.uuid4()}", json=body, headers=h(tenants.a_only))

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


def test_update_only_touches_the_active_organizations_twin(
    client: TestClient, db_session: Session, tenants: Tenants
):
    # Updating A's Anna must leave the identical-looking B Anna untouched.
    response = client.patch(
        f"/api/customers/{tenants.a_anna.id}",
        json={"phone": "999"},
        headers=h(tenants.a_only),
    )

    assert response.status_code == 200
    db_session.refresh(tenants.b_anna)
    assert tenants.b_anna.phone == "070-000 00 00"


@pytest.mark.parametrize("field", ["organization_id", "id", "created_at"])
def test_update_cannot_change_organization_or_identity(
    client: TestClient, db_session: Session, tenants: Tenants, field: str
):
    value = str(tenants.org_b.id) if field == "organization_id" else str(uuid.uuid4())
    response = client.patch(
        f"/api/customers/{tenants.a_anna.id}", json={field: value}, headers=h(tenants.a_only)
    )

    assert response.status_code == 422
    db_session.refresh(tenants.a_anna)
    assert tenants.a_anna.organization_id == tenants.org_a.id


def test_cannot_move_a_customer_into_another_organization_even_if_a_member_of_both(
    client: TestClient, db_session: Session, tenants: Tenants
):
    response = client.patch(
        f"/api/customers/{tenants.a_anna.id}",
        json={"organization_id": str(tenants.org_b.id)},
        headers=h(tenants.shared, tenants.org_a),
    )

    assert response.status_code == 422
    db_session.refresh(tenants.a_anna)
    assert tenants.a_anna.organization_id == tenants.org_a.id


# --- delete --------------------------------------------------------------------------


def test_cannot_delete_foreign_customer(client: TestClient, db_session: Session, tenants: Tenants):
    response = client.delete(f"/api/customers/{tenants.b_anna.id}", headers=h(tenants.a_only))

    assert response.status_code == 404
    assert customer_exists(db_session, tenants.b_anna.id)


def test_delete_of_foreign_uuid_looks_like_delete_of_nonexistent_uuid(
    client: TestClient, tenants: Tenants
):
    foreign = client.delete(f"/api/customers/{tenants.b_anna.id}", headers=h(tenants.a_only))
    missing = client.delete(f"/api/customers/{uuid.uuid4()}", headers=h(tenants.a_only))

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


def test_delete_only_removes_the_active_organizations_record(
    client: TestClient, db_session: Session, tenants: Tenants
):
    response = client.delete(f"/api/customers/{tenants.a_anna.id}", headers=h(tenants.a_only))

    assert response.status_code == 204
    assert not customer_exists(db_session, tenants.a_anna.id)
    assert customer_exists(db_session, tenants.b_anna.id)  # the twin survives


# --- create ----------------------------------------------------------------------------


def test_create_derives_organization_from_the_tenant_context(
    client: TestClient, db_session: Session, tenants: Tenants
):
    response = client.post(
        "/api/customers",
        json={"customer_type": "person", "name": "New In A"},
        headers=h(tenants.a_only),
    )

    assert response.status_code == 201
    created = db_session.get(Customer, uuid.UUID(response.json()["id"]))
    assert created.organization_id == tenants.org_a.id


def test_create_in_selected_organization_for_multi_organization_user(
    client: TestClient, db_session: Session, tenants: Tenants
):
    response = client.post(
        "/api/customers",
        json={"customer_type": "company", "name": "New In B"},
        headers=h(tenants.shared, tenants.org_b),
    )

    created = db_session.get(Customer, uuid.UUID(response.json()["id"]))
    assert created.organization_id == tenants.org_b.id
    listed_in_a = client.get("/api/customers", headers=h(tenants.shared, tenants.org_a))
    assert str(created.id) not in ids(listed_in_a)


@pytest.mark.parametrize("target", ["own", "foreign", "random"])
def test_create_rejects_client_supplied_organization_id(
    client: TestClient, db_session: Session, tenants: Tenants, target: str
):
    org_id = {
        "own": tenants.org_a.id,
        "foreign": tenants.org_b.id,
        "random": uuid.uuid4(),
    }[target]
    before = db_session.scalar(select(func.count()).select_from(Customer))

    response = client.post(
        "/api/customers",
        json={"customer_type": "person", "name": "Sneaky", "organization_id": str(org_id)},
        headers=h(tenants.a_only),
    )

    assert response.status_code == 422
    assert db_session.scalar(select(func.count()).select_from(Customer)) == before


# --- tenant selection still guards every customer route ----------------------------------


def test_selecting_a_foreign_organization_blocks_all_customer_routes(
    client: TestClient, tenants: Tenants
):
    foreign = h(tenants.a_only, tenants.org_b)  # a_only is not a member of B
    target = f"/api/customers/{tenants.b_anna.id}"

    responses = [
        client.get("/api/customers", headers=foreign),
        client.get("/api/customers", params={"q": "anna"}, headers=foreign),
        client.get(target, headers=foreign),
        client.patch(target, json={"name": "x"}, headers=foreign),
        client.delete(target, headers=foreign),
        client.post("/api/customers", json={"customer_type": "person", "name": "x"}, headers=foreign),
    ]

    assert [r.status_code for r in responses] == [404] * 6


def test_customer_routes_require_authentication(client: TestClient, tenants: Tenants):
    target = f"/api/customers/{tenants.a_anna.id}"

    assert client.get("/api/customers").status_code == 401
    assert client.get(target).status_code == 401
    assert client.patch(target, json={"name": "x"}).status_code == 401
    assert client.delete(target).status_code == 401
    assert client.post("/api/customers", json={"customer_type": "person", "name": "x"}).status_code == 401
