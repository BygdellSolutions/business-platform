"""The tenant-isolation contract, run against every resource in tenant_contract.RESOURCES.

Layout mirrors the dev seed:

    shared  - owner of A, admin of B   (multi-organization user)
    a_only  - member of A only
    b_only  - member of B only

Each organization has an identical-looking "twin" record. B also has a `b_unique`
record that only exists in B. Anything that forgets the organization filter shows up
as a wrong id, a duplicate, or a leaked record.
"""

import itertools
import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Organization, Role, User
from tests.factories import add_member, make_org, make_user
from tests.tenant_contract import RESOURCES, Resource, body_for


@dataclass
class World:
    res: Resource
    org_a: Organization
    org_b: Organization
    shared: User
    a_only: User
    b_only: User
    a_twin: Any
    b_twin: Any
    b_unique: Any


@pytest.fixture(params=RESOURCES, ids=lambda r: r.name)
def res(request) -> Resource:
    return request.param


@pytest.fixture
def world(db_session: Session, res: Resource) -> World:
    org_a, org_b = make_org(db_session, "Org A"), make_org(db_session, "Org B")
    shared, a_only, b_only = (make_user(db_session) for _ in range(3))
    add_member(db_session, org_a, shared, Role.OWNER)
    add_member(db_session, org_b, shared, Role.ADMIN)
    add_member(db_session, org_a, a_only, Role.EMPLOYEE)
    add_member(db_session, org_b, b_only, Role.EMPLOYEE)
    return World(
        res=res,
        org_a=org_a,
        org_b=org_b,
        shared=shared,
        a_only=a_only,
        b_only=b_only,
        a_twin=res.make(db_session, org_a),
        b_twin=res.make(db_session, org_b),  # identical-looking
        b_unique=res.make(db_session, org_b, **res.unique_overrides),
    )


def h(user: User, org: Organization | None = None) -> dict[str, str]:
    headers = {"X-Dev-User-Email": user.email}
    if org is not None:
        headers["X-Organization-Id"] = str(org.id)
    return headers


def ids(response) -> set[str]:
    return {row["id"] for row in response.json()}


def count(db: Session, model: type, **where) -> int:
    query = select(func.count()).select_from(model)
    for column, value in where.items():
        query = query.where(getattr(model, column) == value)
    return db.scalar(query)


# --- list and search ---------------------------------------------------------------


def test_list_returns_only_the_active_organizations_records(client: TestClient, world: World):
    a = client.get(world.res.path, headers=h(world.a_only))
    b = client.get(world.res.path, headers=h(world.b_only))

    assert ids(a) == {str(world.a_twin.id)}
    assert ids(b) == {str(world.b_twin.id), str(world.b_unique.id)}


def test_multi_organization_user_sees_only_the_selected_organization(
    client: TestClient, world: World
):
    in_a = client.get(world.res.path, headers=h(world.shared, world.org_a))
    in_b = client.get(world.res.path, headers=h(world.shared, world.org_b))

    assert ids(in_a) == {str(world.a_twin.id)}
    assert ids(in_b) == {str(world.b_twin.id), str(world.b_unique.id)}


def test_search_matches_only_inside_the_active_organization(client: TestClient, world: World):
    q = {"q": world.res.twin_search}
    a = client.get(world.res.path, params=q, headers=h(world.a_only))
    b = client.get(world.res.path, params=q, headers=h(world.b_only))

    assert ids(a) == {str(world.a_twin.id)}
    assert str(world.b_twin.id) in ids(b) and str(world.a_twin.id) not in ids(b)


def test_search_cannot_discover_a_record_that_exists_only_in_another_organization(
    client: TestClient, world: World
):
    q = {"q": world.res.unique_search}
    from_a = client.get(world.res.path, params=q, headers=h(world.a_only))
    from_b = client.get(world.res.path, params=q, headers=h(world.b_only))

    assert from_a.status_code == 200 and from_a.json() == []
    assert ids(from_b) == {str(world.b_unique.id)}


def test_pagination_does_not_spill_into_another_organization(client: TestClient, world: World):
    beyond = client.get(world.res.path, params={"offset": 1}, headers=h(world.a_only))
    assert beyond.json() == []  # A has exactly one record


# --- read --------------------------------------------------------------------------


def test_read_own_record(client: TestClient, world: World):
    response = client.get(f"{world.res.path}/{world.a_twin.id}", headers=h(world.a_only))
    assert response.status_code == 200
    assert response.json()["id"] == str(world.a_twin.id)


def test_foreign_record_is_not_readable_even_with_a_known_uuid(client: TestClient, world: World):
    response = client.get(f"{world.res.path}/{world.b_twin.id}", headers=h(world.a_only))

    assert response.status_code == 404
    assert str(world.b_twin.id) not in response.text


def test_foreign_uuid_is_indistinguishable_from_a_nonexistent_uuid(
    client: TestClient, world: World
):
    foreign = client.get(f"{world.res.path}/{world.b_twin.id}", headers=h(world.a_only))
    missing = client.get(f"{world.res.path}/{uuid.uuid4()}", headers=h(world.a_only))

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    assert foreign.headers["content-length"] == missing.headers["content-length"]


def test_active_organization_is_the_boundary_for_a_multi_organization_user(
    client: TestClient, world: World
):
    own = client.get(f"{world.res.path}/{world.a_twin.id}", headers=h(world.shared, world.org_a))
    cross = client.get(f"{world.res.path}/{world.b_twin.id}", headers=h(world.shared, world.org_a))
    after_switch = client.get(
        f"{world.res.path}/{world.b_twin.id}", headers=h(world.shared, world.org_b)
    )

    assert (own.status_code, cross.status_code, after_switch.status_code) == (200, 404, 200)


# --- update ------------------------------------------------------------------------


def test_foreign_record_cannot_be_updated(client: TestClient, db_session: Session, world: World):
    before = getattr(world.b_twin, world.res.patch_field)

    response = client.patch(
        f"{world.res.path}/{world.b_twin.id}", json=world.res.patch_body, headers=h(world.a_only)
    )

    assert response.status_code == 404
    db_session.refresh(world.b_twin)
    assert getattr(world.b_twin, world.res.patch_field) == before


def test_update_of_a_foreign_uuid_looks_like_update_of_a_nonexistent_uuid(
    client: TestClient, world: World
):
    foreign = client.patch(
        f"{world.res.path}/{world.b_twin.id}", json=world.res.patch_body, headers=h(world.a_only)
    )
    missing = client.patch(
        f"{world.res.path}/{uuid.uuid4()}", json=world.res.patch_body, headers=h(world.a_only)
    )

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


def test_updating_own_record_leaves_the_identical_looking_twin_untouched(
    client: TestClient, db_session: Session, world: World
):
    twin_before = getattr(world.b_twin, world.res.patch_field)

    response = client.patch(
        f"{world.res.path}/{world.a_twin.id}", json=world.res.patch_body, headers=h(world.a_only)
    )

    assert response.status_code == 200
    db_session.refresh(world.a_twin)
    db_session.refresh(world.b_twin)
    assert getattr(world.a_twin, world.res.patch_field) == world.res.patch_value
    assert getattr(world.b_twin, world.res.patch_field) == twin_before


@pytest.mark.parametrize("field", ["organization_id", "id"])
def test_update_cannot_change_organization_or_id(
    client: TestClient, db_session: Session, world: World, field: str
):
    value = str(world.org_b.id) if field == "organization_id" else str(uuid.uuid4())

    for user in (world.a_only, world.shared):  # even a member of both organizations
        response = client.patch(
            f"{world.res.path}/{world.a_twin.id}",
            json={field: value},
            headers=h(user, world.org_a),
        )
        assert response.status_code == 422

    db_session.refresh(world.a_twin)
    assert world.a_twin.organization_id == world.org_a.id


# --- delete ------------------------------------------------------------------------


def test_foreign_record_cannot_be_deleted(client: TestClient, db_session: Session, world: World):
    response = client.delete(f"{world.res.path}/{world.b_twin.id}", headers=h(world.a_only))

    assert response.status_code == 404
    assert count(db_session, world.res.model, id=world.b_twin.id) == 1


def test_delete_of_a_foreign_uuid_looks_like_delete_of_a_nonexistent_uuid(
    client: TestClient, world: World
):
    foreign = client.delete(f"{world.res.path}/{world.b_twin.id}", headers=h(world.a_only))
    missing = client.delete(f"{world.res.path}/{uuid.uuid4()}", headers=h(world.a_only))

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


def test_deleting_own_record_leaves_the_identical_looking_twin(
    client: TestClient, db_session: Session, world: World
):
    response = client.delete(f"{world.res.path}/{world.a_twin.id}", headers=h(world.a_only))

    assert response.status_code == 204
    assert count(db_session, world.res.model, id=world.a_twin.id) == 0
    assert count(db_session, world.res.model, id=world.b_twin.id) == 1


# --- create ------------------------------------------------------------------------


def test_create_takes_the_organization_from_the_tenant_context(
    client: TestClient, db_session: Session, world: World
):
    body = body_for(world.res, db_session, world.org_a)

    response = client.post(world.res.path, json=body, headers=h(world.a_only))

    assert response.status_code == 201
    created = db_session.get(world.res.model, uuid.UUID(response.json()["id"]))
    assert created.organization_id == world.org_a.id


def test_multi_organization_user_creates_in_the_selected_organization_only(
    client: TestClient, db_session: Session, world: World
):
    body = body_for(world.res, db_session, world.org_b)

    response = client.post(world.res.path, json=body, headers=h(world.shared, world.org_b))

    created = db_session.get(world.res.model, uuid.UUID(response.json()["id"]))
    assert created.organization_id == world.org_b.id
    in_a = client.get(world.res.path, headers=h(world.shared, world.org_a))
    assert str(created.id) not in ids(in_a)


@pytest.mark.parametrize("target", ["own", "foreign", "random"])
def test_create_rejects_a_client_supplied_organization_id(
    client: TestClient, db_session: Session, world: World, target: str
):
    org_id = {"own": world.org_a.id, "foreign": world.org_b.id, "random": uuid.uuid4()}[target]
    body = body_for(world.res, db_session, world.org_a)
    before = count(db_session, world.res.model)

    response = client.post(
        world.res.path,
        json={**body, "organization_id": str(org_id)},
        headers=h(world.a_only),
    )

    assert response.status_code == 422
    assert count(db_session, world.res.model) == before


# --- tenant selection and authentication guard every route ------------------------------


def test_selecting_an_organization_you_do_not_belong_to_blocks_every_route(
    client: TestClient, db_session: Session, world: World
):
    body = body_for(world.res, db_session, world.org_a)
    foreign = h(world.a_only, world.org_b)  # a_only is not a member of B
    target = f"{world.res.path}/{world.b_twin.id}"
    path = world.res.path

    responses = [
        client.get(path, headers=foreign),
        client.get(path, params={"q": world.res.twin_search}, headers=foreign),
        client.get(target, headers=foreign),
        client.patch(target, json=world.res.patch_body, headers=foreign),
        client.delete(target, headers=foreign),
        client.post(path, json=body, headers=foreign),
    ]

    assert [r.status_code for r in responses] == [404] * 6


def test_every_route_requires_authentication(client: TestClient, db_session: Session, world: World):
    body = body_for(world.res, db_session, world.org_a)
    target = f"{world.res.path}/{world.a_twin.id}"
    path = world.res.path

    responses = [
        client.get(path),
        client.get(target),
        client.patch(target, json=world.res.patch_body),
        client.delete(target),
        client.post(path, json=body),
    ]

    assert [r.status_code for r in responses] == [401] * 5


# --- resources do not leak into each other -------------------------------------------------


@pytest.mark.parametrize(
    "owner,other",
    list(itertools.permutations(RESOURCES, 2)),
    ids=lambda r: r.name,
)
def test_a_record_id_does_not_resolve_through_another_resources_route(
    client: TestClient, db_session: Session, owner: Resource, other: Resource
):
    org = make_org(db_session)
    user = make_user(db_session)
    add_member(db_session, org, user)
    record = owner.make(db_session, org)

    response = client.get(f"{other.path}/{record.id}", headers=h(user))

    assert response.status_code == 404
