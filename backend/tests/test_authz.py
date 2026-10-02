"""Authorization uses the role of the ACTIVE membership only.

One user is OWNER in org A, VIEWER in org B, ADMIN in org C and EMPLOYEE in org D. The
same request must succeed or fail depending only on which organization it is scoped to.
"""

import uuid
from dataclasses import dataclass

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.authz import require_role
from app.core.tenant import TenantContext
from app.models import Organization, Role, User
from tests.factories import add_member, make_definition, make_org, make_transaction, make_user

BASE = "/api/custom-fields"
ADMINISTRATORS = {Role.OWNER, Role.ADMIN}


# --- the helper itself -----------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(Role))
def test_require_role_allows_exactly_the_listed_roles(role: Role):
    ctx = TenantContext(user=None, organization_id=uuid.uuid4(), role=role)  # type: ignore[arg-type]

    if role in ADMINISTRATORS:
        require_role(ctx, ADMINISTRATORS)
    else:
        with pytest.raises(HTTPException) as refused:
            require_role(ctx, ADMINISTRATORS)
        assert refused.value.status_code == 403


def test_require_role_accepts_any_iterable():
    ctx = TenantContext(user=None, organization_id=uuid.uuid4(), role=Role.VIEWER)  # type: ignore[arg-type]
    require_role(ctx, (Role.VIEWER,))
    require_role(ctx, [Role.VIEWER, Role.OWNER])
    require_role(ctx, frozenset({Role.VIEWER}))


# --- one user, four organizations ------------------------------------------------------------------------


@dataclass
class World:
    user: User
    orgs: dict[str, Organization]  # "A".."D"
    roles: dict[str, Role]

    def headers(self, org: str) -> dict[str, str]:
        return {"X-Dev-User-Email": self.user.email, "X-Organization-Id": str(self.orgs[org].id)}


@pytest.fixture
def world(db_session: Session) -> World:
    user = make_user(db_session)
    roles = {"A": Role.OWNER, "B": Role.VIEWER, "C": Role.ADMIN, "D": Role.EMPLOYEE}
    orgs = {}
    for name, role in roles.items():
        orgs[name] = make_org(db_session, f"Org {name}")
        add_member(db_session, orgs[name], user, role)
    return World(user, orgs, roles)


NEW_DEFINITION = {"entity_type": "transaction_line", "key": "comment", "label": "Comment", "field_type": "text"}


@pytest.mark.parametrize("org,allowed", [("A", True), ("B", False), ("C", True), ("D", False)])
def test_creating_a_definition_depends_on_the_role_in_the_active_organization(
    client: TestClient, world: World, org: str, allowed: bool
):
    response = client.post(f"{BASE}/definitions", json=NEW_DEFINITION, headers=world.headers(org))

    assert response.status_code == (201 if allowed else 403), response.text


@pytest.mark.parametrize("org,allowed", [("A", True), ("B", False), ("C", True), ("D", False)])
def test_changing_a_definition_depends_on_the_role_in_the_active_organization(
    client: TestClient, db_session: Session, world: World, org: str, allowed: bool
):
    definition = make_definition(db_session, world.orgs[org], key="comment")

    response = client.patch(f"{BASE}/definitions/{definition.id}", json={"label": "Renamed"}, headers=world.headers(org))

    assert response.status_code == (200 if allowed else 403)
    db_session.refresh(definition)
    assert definition.label == ("Renamed" if allowed else "Comment")


@pytest.mark.parametrize("org,allowed", [("A", True), ("B", False), ("C", True), ("D", False)])
def test_options_are_administered_by_owners_and_admins_only(
    client: TestClient, db_session: Session, world: World, org: str, allowed: bool
):
    definition = make_definition(db_session, world.orgs[org], key="grade", field_type="select", options=["Low"])
    option_id = client.get(f"{BASE}/definitions/{definition.id}", headers=world.headers(org)).json()["options"][0]["id"]
    expected = 201 if allowed else 403

    added = client.post(f"{BASE}/definitions/{definition.id}/options", json={"label": "High"}, headers=world.headers(org))
    changed = client.patch(f"{BASE}/definitions/{definition.id}/options/{option_id}", json={"label": "Min"}, headers=world.headers(org))

    assert added.status_code == expected
    assert changed.status_code == (200 if allowed else 403)


def test_a_role_held_in_another_organization_never_carries_over(client: TestClient, db_session: Session, world: World):
    # OWNER in A, but VIEWER in B: the definition in B must stay untouchable from B ...
    in_b = make_definition(db_session, world.orgs["B"], key="comment")
    assert client.patch(f"{BASE}/definitions/{in_b.id}", json={"label": "x"}, headers=world.headers("B")).status_code == 403
    # ... and from A it does not even exist (isolation, not authorization).
    assert client.patch(f"{BASE}/definitions/{in_b.id}", json={"label": "x"}, headers=world.headers("A")).status_code == 404


def test_the_role_comes_from_the_selected_organization_even_for_the_same_request(client: TestClient, world: World):
    results = {
        org: client.post(f"{BASE}/definitions", json={**NEW_DEFINITION, "key": f"k_{org.lower()}"}, headers=world.headers(org)).status_code
        for org in "ABCD"
    }

    assert results == {"A": 201, "B": 403, "C": 201, "D": 403}


def test_a_user_with_several_memberships_must_select_one_before_any_role_applies(client: TestClient, world: World):
    response = client.post(f"{BASE}/definitions", json=NEW_DEFINITION, headers={"X-Dev-User-Email": world.user.email})

    assert response.status_code == 400  # not silently "the best role"


def test_a_non_member_gets_404_not_403(client: TestClient, db_session: Session, world: World):
    outsider = make_user(db_session)
    headers = {"X-Dev-User-Email": outsider.email, "X-Organization-Id": str(world.orgs["A"].id)}

    assert client.post(f"{BASE}/definitions", json=NEW_DEFINITION, headers=headers).status_code == 404


@pytest.mark.parametrize("role", list(Role))
def test_every_role_in_a_single_organization(client: TestClient, db_session: Session, role: Role):
    org = make_org(db_session, f"Solo {role}")
    user = make_user(db_session)
    add_member(db_session, org, user, role)
    headers = {"X-Dev-User-Email": user.email}

    response = client.post(f"{BASE}/definitions", json=NEW_DEFINITION, headers=headers)

    assert response.status_code == (201 if role in ADMINISTRATORS else 403)


# --- what every member may still do ------------------------------------------------------------------------------


@pytest.mark.parametrize("org", ["A", "B", "C", "D"])
def test_every_member_can_read_definitions_choices_and_metadata(client: TestClient, db_session: Session, world: World, org: str):
    definition = make_definition(db_session, world.orgs[org], key="grade", field_type="select", options=["Low"])
    headers = world.headers(org)

    assert client.get(f"{BASE}/definitions", headers=headers).status_code == 200
    assert client.get(f"{BASE}/definitions/{definition.id}", headers=headers).status_code == 200
    assert client.get(f"{BASE}/definitions/{definition.id}/choices", headers=headers).status_code == 200
    assert client.get(f"{BASE}/entity-types", headers=headers).status_code == 200


@pytest.mark.parametrize("org", ["A", "B", "C", "D"])
def test_every_member_can_write_values_roles_do_not_restrict_that(client: TestClient, db_session: Session, world: World, org: str):
    make_definition(db_session, world.orgs[org], key="comment")
    tx = make_transaction(db_session, world.orgs[org], lines=[{}])
    line_id = tx.id  # any record id would do for the header type below
    make_definition(db_session, world.orgs[org], entity_type="transaction", key="memo")

    response = client.patch(
        f"{BASE}/entities/transaction/{line_id}/values", json={"values": {"memo": "ok"}}, headers=world.headers(org)
    )

    assert response.status_code == 200
