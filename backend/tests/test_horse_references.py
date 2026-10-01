"""Horse -> Customer references: tenant safety, inactive customers, deletion, and the
separation of owner, stable and (future) billing customer."""

import uuid
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import engine
from app.models import Customer, Organization, Role, User
from app.modules.equine.models import Horse
from tests.factories import add_member, make_customer, make_horse, make_org, make_user


@dataclass
class World:
    org_a: Organization
    org_b: Organization
    user_a: User
    user_b: User
    anna_a: Customer  # identical-looking customers in both organizations
    anna_b: Customer
    hk_a: Customer
    hk_b: Customer


@pytest.fixture
def world(db_session: Session) -> World:
    org_a, org_b = make_org(db_session, "A"), make_org(db_session, "B")
    user_a, user_b = make_user(db_session), make_user(db_session)
    add_member(db_session, org_a, user_a, Role.EMPLOYEE)
    add_member(db_session, org_b, user_b, Role.EMPLOYEE)
    return World(
        org_a=org_a,
        org_b=org_b,
        user_a=user_a,
        user_b=user_b,
        anna_a=make_customer(db_session, org_a, "Anna Andersson"),
        anna_b=make_customer(db_session, org_b, "Anna Andersson"),
        hk_a=make_customer(db_session, org_a, "Umeå HK", email=None),
        hk_b=make_customer(db_session, org_b, "Umeå HK", email=None),
    )


def h(user: User) -> dict[str, str]:
    return {"X-Dev-User-Email": user.email}


def horse_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Horse))


# --- API: references must exist in the active organization ----------------------------------


@pytest.mark.parametrize("field", ["owner_customer_id", "stable_customer_id"])
def test_create_rejects_a_customer_from_another_organization(
    client: TestClient, db_session: Session, world: World, field: str
):
    payload = {"name": "Kalle", "owner_customer_id": str(world.anna_a.id)}
    payload[field] = str(world.anna_b.id)  # B's customer, used from A
    before = horse_count(db_session)

    response = client.post("/api/horses", json=payload, headers=h(world.user_a))

    assert response.status_code == 422
    error = response.json()["detail"][0]
    assert error["loc"] == ["body", field] and error["type"] == "reference.not_found"
    assert str(world.anna_b.id) not in response.text
    assert horse_count(db_session) == before


@pytest.mark.parametrize("field", ["owner_customer_id", "stable_customer_id"])
def test_foreign_customer_is_indistinguishable_from_a_nonexistent_one(
    client: TestClient, world: World, field: str
):
    def attempt(customer_id):
        payload = {"name": "Kalle", "owner_customer_id": str(world.anna_a.id)}
        payload[field] = str(customer_id)
        return client.post("/api/horses", json=payload, headers=h(world.user_a))

    foreign, missing = attempt(world.anna_b.id), attempt(uuid.uuid4())

    assert foreign.status_code == missing.status_code == 422
    assert foreign.json() == missing.json()


@pytest.mark.parametrize("field", ["owner_customer_id", "stable_customer_id"])
def test_update_rejects_a_customer_from_another_organization(
    client: TestClient, db_session: Session, world: World, field: str
):
    horse = make_horse(db_session, world.org_a, owner=world.anna_a, stable=world.hk_a)
    before = (horse.owner_customer_id, horse.stable_customer_id)

    foreign = client.patch(
        f"/api/horses/{horse.id}", json={field: str(world.anna_b.id)}, headers=h(world.user_a)
    )
    missing = client.patch(
        f"/api/horses/{horse.id}", json={field: str(uuid.uuid4())}, headers=h(world.user_a)
    )

    assert foreign.status_code == missing.status_code == 422
    assert foreign.json() == missing.json()
    db_session.refresh(horse)
    assert (horse.owner_customer_id, horse.stable_customer_id) == before


def test_update_can_move_to_another_customer_of_the_same_organization(
    client: TestClient, db_session: Session, world: World
):
    erik = make_customer(db_session, world.org_a, "Erik Svensson")
    horse = make_horse(db_session, world.org_a, owner=world.anna_a)

    response = client.patch(
        f"/api/horses/{horse.id}",
        json={"owner_customer_id": str(erik.id), "stable_customer_id": str(world.hk_a.id)},
        headers=h(world.user_a),
    )

    assert response.status_code == 200
    assert response.json()["owner"]["name"] == "Erik Svensson"
    assert response.json()["stable"]["id"] == str(world.hk_a.id)


def test_owner_summary_comes_from_the_horses_own_organization(
    client: TestClient, db_session: Session, world: World
):
    # Both organizations have an "Anna Andersson": each horse must show ITS Anna.
    horse_a = make_horse(db_session, world.org_a, owner=world.anna_a)
    horse_b = make_horse(db_session, world.org_b, owner=world.anna_b)

    a = client.get(f"/api/horses/{horse_a.id}", headers=h(world.user_a)).json()
    b = client.get(f"/api/horses/{horse_b.id}", headers=h(world.user_b)).json()

    assert a["owner"]["id"] == str(world.anna_a.id)
    assert b["owner"]["id"] == str(world.anna_b.id)


# --- database: composite foreign keys refuse cross-tenant links even without the API ----------


def test_database_rejects_a_foreign_owner(db_session: Session, world: World):
    with pytest.raises(IntegrityError), db_session.begin_nested():
        make_horse(db_session, world.org_a, owner=world.anna_b)


def test_database_rejects_a_foreign_stable(db_session: Session, world: World):
    with pytest.raises(IntegrityError), db_session.begin_nested():
        make_horse(db_session, world.org_a, owner=world.anna_a, stable=world.hk_b)


def test_database_accepts_same_organization_references_and_a_null_stable(
    db_session: Session, world: World
):
    assert make_horse(db_session, world.org_a, owner=world.anna_a, stable=world.hk_a).id
    assert make_horse(db_session, world.org_a, owner=world.anna_a, stable=None).id


def test_customers_have_the_unique_pair_that_composite_keys_reference():
    uniques = sa.inspect(engine).get_unique_constraints("customers")
    assert ["organization_id", "id"] in [u["column_names"] for u in uniques]


# --- inactive customers ----------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["owner_customer_id", "stable_customer_id"])
def test_cannot_assign_an_inactive_customer_on_create(
    client: TestClient, db_session: Session, world: World, field: str
):
    inactive = make_customer(db_session, world.org_a, "Gone AB", active=False)
    payload = {"name": "Kalle", "owner_customer_id": str(world.anna_a.id)}
    payload[field] = str(inactive.id)

    response = client.post("/api/horses", json=payload, headers=h(world.user_a))

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "reference.inactive"


def test_existing_references_survive_deactivation(
    client: TestClient, db_session: Session, world: World
):
    horse = make_horse(db_session, world.org_a, owner=world.anna_a, stable=world.hk_a)
    headers = h(world.user_a)

    client.patch(f"/api/customers/{world.anna_a.id}", json={"active": False}, headers=headers)
    client.patch(f"/api/customers/{world.hk_a.id}", json={"active": False}, headers=headers)

    shown = client.get(f"/api/horses/{horse.id}", headers=headers)
    assert shown.status_code == 200
    assert shown.json()["owner"]["active"] is False and shown.json()["stable"]["active"] is False

    # Editing the horse is not blocked by its deactivated owner...
    assert client.patch(f"/api/horses/{horse.id}", json={"name": "Kalle II"}, headers=headers).status_code == 200
    # ...and re-sending the unchanged reference is fine.
    same = client.patch(
        f"/api/horses/{horse.id}", json={"owner_customer_id": str(world.anna_a.id)}, headers=headers
    )
    assert same.status_code == 200


def test_cannot_move_a_horse_to_an_inactive_customer(
    client: TestClient, db_session: Session, world: World
):
    inactive = make_customer(db_session, world.org_a, "Gone AB", active=False)
    horse = make_horse(db_session, world.org_a, owner=world.anna_a)

    response = client.patch(
        f"/api/horses/{horse.id}", json={"owner_customer_id": str(inactive.id)}, headers=h(world.user_a)
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "reference.inactive"
    db_session.refresh(horse)
    assert horse.owner_customer_id == world.anna_a.id


def test_reactivated_customer_can_be_assigned_again(
    client: TestClient, db_session: Session, world: World
):
    inactive = make_customer(db_session, world.org_a, "Back AB", active=False)
    horse = make_horse(db_session, world.org_a, owner=world.anna_a)
    headers = h(world.user_a)
    move = {"owner_customer_id": str(inactive.id)}

    assert client.patch(f"/api/horses/{horse.id}", json=move, headers=headers).status_code == 422
    client.patch(f"/api/customers/{inactive.id}", json={"active": True}, headers=headers)
    assert client.patch(f"/api/horses/{horse.id}", json=move, headers=headers).status_code == 200


# --- deleting a referenced customer ------------------------------------------------------------------


@pytest.mark.parametrize("role", ["owner", "stable"])
def test_a_referenced_customer_cannot_be_deleted(
    client: TestClient, db_session: Session, world: World, role: str
):
    if role == "owner":
        horse, target = make_horse(db_session, world.org_a, owner=world.anna_a), world.anna_a
    else:
        horse, target = make_horse(db_session, world.org_a, owner=world.anna_a, stable=world.hk_a), world.hk_a

    response = client.delete(f"/api/customers/{target.id}", headers=h(world.user_a))

    assert response.status_code == 409
    assert "horse" not in response.text.lower()  # Customers does not know about Equine
    assert db_session.get(Customer, target.id) is not None
    assert db_session.get(Horse, horse.id) is not None
    assert client.get(f"/api/customers/{target.id}", headers=h(world.user_a)).status_code == 200


def test_customer_can_be_deleted_once_no_longer_referenced(
    client: TestClient, db_session: Session, world: World
):
    horse = make_horse(db_session, world.org_a, owner=world.anna_a, stable=world.hk_a)
    headers = h(world.user_a)

    assert client.delete(f"/api/customers/{world.hk_a.id}", headers=headers).status_code == 409
    client.patch(f"/api/horses/{horse.id}", json={"stable_customer_id": None}, headers=headers)
    assert client.delete(f"/api/customers/{world.hk_a.id}", headers=headers).status_code == 204

    assert client.delete(f"/api/customers/{world.anna_a.id}", headers=headers).status_code == 409
    client.delete(f"/api/horses/{horse.id}", headers=headers)
    assert client.delete(f"/api/customers/{world.anna_a.id}", headers=headers).status_code == 204


def test_a_refused_delete_does_not_break_the_session(
    client: TestClient, db_session: Session, world: World
):
    make_horse(db_session, world.org_a, owner=world.anna_a)
    headers = h(world.user_a)

    client.delete(f"/api/customers/{world.anna_a.id}", headers=headers)

    # The same request context still works afterwards.
    assert client.get("/api/customers", headers=headers).status_code == 200


# --- owner, stable and billing are separate concepts -------------------------------------------------


def test_owner_and_stable_are_independent(client: TestClient, db_session: Session, world: World):
    headers = h(world.user_a)

    same = client.post(
        "/api/horses",
        json={"name": "Same", "owner_customer_id": str(world.hk_a.id), "stable_customer_id": str(world.hk_a.id)},
        headers=headers,
    )
    different = client.post(
        "/api/horses",
        json={"name": "Different", "owner_customer_id": str(world.anna_a.id), "stable_customer_id": str(world.hk_a.id)},
        headers=headers,
    )
    no_stable = client.post(
        "/api/horses", json={"name": "None", "owner_customer_id": str(world.anna_a.id)}, headers=headers
    )

    assert (same.status_code, different.status_code, no_stable.status_code) == (201, 201, 201)
    assert different.json()["owner"]["id"] != different.json()["stable"]["id"]
    assert no_stable.json()["stable"] is None  # a stable is never inferred from the owner


def test_a_horse_has_no_billing_customer(client: TestClient, db_session: Session, world: World):
    horse = make_horse(db_session, world.org_a, owner=world.anna_a, stable=world.hk_a)
    headers = h(world.user_a)

    shown = client.get(f"/api/horses/{horse.id}", headers=headers).json()

    assert not [key for key in shown if "bill" in key]
    assert not hasattr(Horse, "billing_customer_id")
    for body in (
        {"name": "x", "owner_customer_id": str(world.anna_a.id), "billing_customer_id": str(world.hk_a.id)},
    ):
        assert client.post("/api/horses", json=body, headers=headers).status_code == 422
    assert (
        client.patch(
            f"/api/horses/{horse.id}", json={"billing_customer_id": str(world.hk_a.id)}, headers=headers
        ).status_code
        == 422
    )


# --- filters are tenant safe -----------------------------------------------------------------------------


def test_owner_filter_never_crosses_organizations(
    client: TestClient, db_session: Session, world: World
):
    horse_a = make_horse(db_session, world.org_a, owner=world.anna_a)
    make_horse(db_session, world.org_b, owner=world.anna_b)  # identical-looking Kalle

    own = client.get("/api/horses", params={"owner_customer_id": str(world.anna_a.id)}, headers=h(world.user_a))
    foreign = client.get("/api/horses", params={"owner_customer_id": str(world.anna_b.id)}, headers=h(world.user_a))
    missing = client.get("/api/horses", params={"owner_customer_id": str(uuid.uuid4())}, headers=h(world.user_a))

    assert [x["id"] for x in own.json()] == [str(horse_a.id)]
    assert foreign.status_code == missing.status_code == 200
    assert foreign.json() == missing.json() == []
