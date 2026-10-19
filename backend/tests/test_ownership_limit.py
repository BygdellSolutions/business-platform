"""How many organizations an account may CREATE, and that owning an existing organization is never limited.

The limit (`users.max_owned_organizations`, a historical name) counts only organizations the account created. An
account that may create none (every invited person) can still become the owner of an organization that already
exists: by a promotion, a transfer or an owner invitation. Creating is covered in tests/test_organization_creation.py.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core import memberships
from app.models import OrganizationCreationRequest, OrganizationUser, Role, User
from app.scripts import admin
from tests.factories import add_member, make_org, make_user
from tests.test_migration_currency import _execute, _scalar, _upgrade, scratch_url  # noqa: F401


def _h(user: User, org=None) -> dict[str, str]:
    return {"X-Dev-User-Email": user.email, **({"X-Organization-Id": str(org.id)} if org else {})}


def _owner_elsewhere(db: Session, user: User) -> None:
    """Make `user` own one organization already (one it did not create)."""
    add_member(db, make_org(db, "Already owned"), user, Role.OWNER)


def _role(db: Session, org, user) -> str | None:
    db.expire_all()
    return db.scalar(select(OrganizationUser.role).where(OrganizationUser.organization_id == org.id, OrganizationUser.user_id == user.id))


@pytest.fixture
def team(db_session: Session):
    org = make_org(db_session, "Team")
    owner, member = make_user(db_session, name="Owner"), make_user(db_session, name="Member")  # both may create none
    add_member(db_session, org, owner, Role.OWNER)
    membership = add_member(db_session, org, member, Role.EMPLOYEE)
    return org, owner, member, membership


def test_a_member_who_may_create_none_can_be_promoted_to_owner(client: TestClient, db_session: Session, team):
    org, owner, member, membership = team
    _owner_elsewhere(db_session, member)

    assert client.patch(f"/api/members/{membership.id}", json={"role": "owner"}, headers=_h(owner, org)).status_code == 200
    assert _role(db_session, org, member) == "owner"


def test_ownership_can_be_transferred_to_someone_who_may_create_none(client: TestClient, db_session: Session, team):
    org, owner, member, membership = team
    _owner_elsewhere(db_session, member)

    response = client.post("/api/organization/transfer-ownership", json={"membership_id": str(membership.id)}, headers=_h(owner, org))

    assert response.status_code == 204
    assert _role(db_session, org, member) == "owner" and _role(db_session, org, owner) == "admin"


def test_an_owner_invitation_is_accepted_by_someone_who_may_create_none(client: TestClient, db_session: Session, team):
    org, owner, _, _ = team
    invited = make_user(db_session, email="limit.zero@invitees.invalid")
    _owner_elsewhere(db_session, invited)
    token = client.post("/api/invitations", json={"email": invited.email, "role": "owner"}, headers=_h(owner, org)).json()["token"]

    assert client.post("/api/invite/accept", json={"token": token}, headers=_h(invited)).status_code == 200
    assert _role(db_session, org, invited) == "owner"


def test_becoming_an_owner_does_not_use_the_creation_allowance(client: TestClient, db_session: Session, team):
    org, owner, member, membership = team
    member.max_owned_organizations = 1
    db_session.flush()
    assert client.patch(f"/api/members/{membership.id}", json={"role": "owner"}, headers=_h(owner, org)).status_code == 200

    body = client.get("/api/me/user", headers=_h(member)).json()
    assert (body["created_organizations"], body["can_create_organizations"]) == (0, True)


def test_the_database_no_longer_caps_owner_memberships(db_session: Session, team):
    org, _, member, membership = team
    _owner_elsewhere(db_session, member)
    db_session.execute(text("UPDATE organization_users SET role = 'owner' WHERE id = :m"), {"m": membership.id})
    assert _role(db_session, org, member) == "owner"


def test_the_operators_repair_still_makes_an_owner(db_session: Session):
    ownerless = make_org(db_session, "Legacy ownerless")
    member = make_user(db_session)
    add_member(db_session, ownerless, member, Role.ADMIN)

    memberships.repair_owner(db_session, organization_id=ownerless.id, user_id=member.id, now=datetime.now(timezone.utc))

    assert _role(db_session, ownerless, member) == "owner"


def _created(db: Session, user: User, name: str) -> None:
    """An organization `user` created (what POST /api/organizations writes): owner membership and creation record."""
    org = make_org(db, name)
    add_member(db, org, user, Role.OWNER)
    db.add(OrganizationCreationRequest(user_id=user.id, request_key="k" * 42 + name[0], request_hash="0" * 64, organization_id=org.id))
    db.flush()


def test_me_reports_created_and_allowed(client: TestClient, db_session: Session):
    user = make_user(db_session, max_owned_organizations=3)
    _created(db_session, user, "Mine")
    _owner_elsewhere(db_session, user)  # owned, not created: does not count
    add_member(db_session, make_org(db_session, "Member only"), user, Role.VIEWER)

    body = client.get("/api/me/user", headers=_h(user)).json()

    assert (body["created_organizations"], body["max_owned_organizations"], body["can_create_organizations"]) == (1, 3, True)


def test_the_operator_sets_the_limit_and_grant_revoke_count_created_organizations(db_session: Session):
    user = make_user(db_session, email="limits@tests.invalid")
    _created(db_session, user, "Created")
    _owner_elsewhere(db_session, user)

    admin.set_owned_limit(db_session, email=user.email, limit=5)
    assert db_session.get(User, user.id).max_owned_organizations == 5
    admin.set_org_creation(db_session, email=user.email, allowed=False)  # revoke: exactly what it created
    assert db_session.get(User, user.id).max_owned_organizations == 1
    admin.set_org_creation(db_session, email=user.email, allowed=True)  # grant: room for one more
    assert db_session.get(User, user.id).max_owned_organizations == 2
    with pytest.raises(admin.OperatorError):
        admin.set_owned_limit(db_session, email=user.email, limit=-1)


BEFORE = "c3e5a7b9d024"


def test_the_migration_gives_everyone_at_least_their_current_ownership(scratch_url: str):  # noqa: F811
    _upgrade(scratch_url, BEFORE)
    for org in ("a", "b", "c"):
        _execute(scratch_url, f"INSERT INTO organizations (id, name) VALUES ('00000000-0000-4000-8000-00000000000{ord(org) - 96}', '{org}')")
    _execute(scratch_url, "INSERT INTO users (id, email, name, can_create_organizations) VALUES ('00000000-0000-4000-8000-0000000000f1', 'many@x.test', 'Many', true)")
    _execute(scratch_url, "INSERT INTO users (id, email, name, can_create_organizations) VALUES ('00000000-0000-4000-8000-0000000000f2', 'none@x.test', 'None', false)")
    for n in (1, 2, 3):
        _execute(
            scratch_url,
            f"INSERT INTO organization_users (organization_id, user_id, role) VALUES ('00000000-0000-4000-8000-00000000000{n}', '00000000-0000-4000-8000-0000000000f1', 'owner')",
        )

    _upgrade(scratch_url, "head")

    assert _scalar(scratch_url, "SELECT max_owned_organizations FROM users WHERE email = 'many@x.test'") == 3  # nobody is over the limit
    assert _scalar(scratch_url, "SELECT max_owned_organizations FROM users WHERE email = 'none@x.test'") == 1  # the default account


def test_the_creation_limit_migration_keeps_limits_drops_the_cap_and_defaults_new_accounts_to_none(scratch_url: str):  # noqa: F811
    _upgrade(scratch_url, "b8d0f2a4c613")
    _execute(scratch_url, "INSERT INTO users (id, email, name, max_owned_organizations) VALUES ('00000000-0000-4000-8000-0000000000e1', 'kept@x.test', 'Kept', 3)")
    # created earlier without a retry key: an owner membership and the security event, but no creation record
    _execute(scratch_url, "INSERT INTO organizations (id, name, default_currency) VALUES ('00000000-0000-4000-8000-0000000000d1', 'Keyless', 'SEK')")
    _execute(scratch_url, "INSERT INTO organization_users (organization_id, user_id, role) VALUES ('00000000-0000-4000-8000-0000000000d1', '00000000-0000-4000-8000-0000000000e1', 'owner')")
    _execute(
        scratch_url,
        "INSERT INTO security_events (occurred_at, event_type, actor_user_id, organization_id) VALUES (now(), 'organization_created', '00000000-0000-4000-8000-0000000000e1', '00000000-0000-4000-8000-0000000000d1')",
    )

    _upgrade(scratch_url, "head")

    assert _scalar(scratch_url, "SELECT count(*) FROM organization_creation_requests WHERE user_id = '00000000-0000-4000-8000-0000000000e1' AND organization_id = '00000000-0000-4000-8000-0000000000d1'") == 1

    assert _scalar(scratch_url, "SELECT max_owned_organizations FROM users WHERE email = 'kept@x.test'") == 3  # left as it was
    _execute(scratch_url, "INSERT INTO users (id, email, name) VALUES ('00000000-0000-4000-8000-0000000000e2', 'new@x.test', 'New')")
    assert _scalar(scratch_url, "SELECT max_owned_organizations FROM users WHERE email = 'new@x.test'") == 0
    assert _scalar(scratch_url, "SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_organization_users_owner_limit'") == 0
