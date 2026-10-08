"""How many organizations an account may OWN, on every path to ownership.

Owned 1 / 1 cannot gain another organization: not by creating one (tests/test_organization_creation.py), not by a
promotion, not by a transfer, not by accepting an owner invitation. Membership costs nothing. A database trigger
refuses a path that forgets the application check; only the operator's repair is allowed past it.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core import memberships
from app.models import OrganizationInvitation, OrganizationUser, Role, User
from app.scripts import admin
from tests.factories import add_member, make_org, make_user
from tests.test_migration_currency import _execute, _scalar, _upgrade, scratch_url  # noqa: F401


def _h(user: User, org=None) -> dict[str, str]:
    return {"X-Dev-User-Email": user.email, **({"X-Organization-Id": str(org.id)} if org else {})}


def _owner_elsewhere(db: Session, user: User) -> None:
    """Make `user` own one organization already (Owned 1 / 1 with the default limit)."""
    add_member(db, make_org(db, "Already owned"), user, Role.OWNER)


def _role(db: Session, org, user) -> str | None:
    db.expire_all()
    return db.scalar(select(OrganizationUser.role).where(OrganizationUser.organization_id == org.id, OrganizationUser.user_id == user.id))


@pytest.fixture
def team(db_session: Session):
    org = make_org(db_session, "Team")
    owner, member = make_user(db_session, name="Owner"), make_user(db_session, name="Member")
    add_member(db_session, org, owner, Role.OWNER)
    membership = add_member(db_session, org, member, Role.EMPLOYEE)
    return org, owner, member, membership


def test_a_member_at_the_limit_cannot_be_promoted_to_owner(client: TestClient, db_session: Session, team):
    org, owner, member, membership = team
    _owner_elsewhere(db_session, member)

    response = client.patch(f"/api/members/{membership.id}", json={"role": "owner"}, headers=_h(owner, org))

    assert response.status_code == 409 and response.json()["detail"]["code"] == "ownership_limit_reached"
    assert _role(db_session, org, member) == "employee"


def test_a_member_below_the_limit_can_be_promoted(client: TestClient, db_session: Session, team):
    org, owner, member, membership = team
    assert client.patch(f"/api/members/{membership.id}", json={"role": "owner"}, headers=_h(owner, org)).status_code == 200
    assert _role(db_session, org, member) == "owner"


def test_ownership_cannot_be_transferred_to_someone_at_the_limit(client: TestClient, db_session: Session, team):
    org, owner, member, membership = team
    _owner_elsewhere(db_session, member)

    response = client.post("/api/organization/transfer-ownership", json={"membership_id": str(membership.id)}, headers=_h(owner, org))

    assert response.status_code == 409 and response.json()["detail"]["code"] == "ownership_limit_reached"
    assert _role(db_session, org, owner) == "owner" and _role(db_session, org, member) == "employee"


def test_a_larger_limit_allows_the_transfer(client: TestClient, db_session: Session, team):
    org, owner, member, membership = team
    _owner_elsewhere(db_session, member)
    db_session.execute(text("UPDATE users SET max_owned_organizations = 2 WHERE id = :u"), {"u": member.id})

    response = client.post("/api/organization/transfer-ownership", json={"membership_id": str(membership.id)}, headers=_h(owner, org))

    assert response.status_code == 204
    assert _role(db_session, org, member) == "owner"


def test_an_owner_invitation_at_the_limit_is_refused_whole_and_stays_pending(client: TestClient, db_session: Session, team):
    org, owner, _, _ = team
    invited = make_user(db_session, email="limit.reached@invitees.invalid")
    _owner_elsewhere(db_session, invited)
    token = client.post("/api/invitations", json={"email": invited.email, "role": "owner"}, headers=_h(owner, org)).json()["token"]

    response = client.post("/api/invite/accept", json={"token": token}, headers=_h(invited))

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "ownership_limit_reached",
        "message": "You cannot become an owner of this organization because you have reached your owned-organization limit.",
    }
    assert _role(db_session, org, invited) is None  # no silent downgrade to another role
    invitation = db_session.scalar(select(OrganizationInvitation).where(OrganizationInvitation.email == invited.email))
    assert invitation.accepted_at is None and invitation.revoked_at is None  # still pending

    db_session.execute(text("UPDATE users SET max_owned_organizations = 2 WHERE id = :u"), {"u": invited.id})
    assert client.post("/api/invite/accept", json={"token": token}, headers=_h(invited)).status_code == 200  # once the limit allows
    assert _role(db_session, org, invited) == "owner"


def test_a_non_owner_invitation_is_unaffected_by_the_limit(client: TestClient, db_session: Session, team):
    org, owner, _, _ = team
    invited = make_user(db_session, email="just.a.member@invitees.invalid", max_owned_organizations=0)
    token = client.post("/api/invitations", json={"email": invited.email, "role": "employee"}, headers=_h(owner, org)).json()["token"]

    assert client.post("/api/invite/accept", json={"token": token}, headers=_h(invited)).status_code == 200
    assert _role(db_session, org, invited) == "employee"


def test_the_database_refuses_an_owner_beyond_the_limit_even_without_the_application(db_session: Session, team):
    org, _, member, membership = team
    _owner_elsewhere(db_session, member)
    with pytest.raises(DBAPIError, match="owned organization limit"):
        with db_session.begin_nested():
            db_session.execute(text("UPDATE organization_users SET role = 'owner' WHERE id = :m"), {"m": membership.id})
    other = make_org(db_session, "Another")
    with pytest.raises(DBAPIError, match="owned organization limit"):
        with db_session.begin_nested():
            db_session.execute(
                text("INSERT INTO organization_users (organization_id, user_id, role) VALUES (:o, :u, 'owner')"), {"o": other.id, "u": member.id}
            )


def test_the_operators_repair_is_the_one_override(db_session: Session):
    ownerless = make_org(db_session, "Legacy ownerless")
    member = make_user(db_session)
    _owner_elsewhere(db_session, member)
    add_member(db_session, ownerless, member, Role.ADMIN)

    memberships.repair_owner(db_session, organization_id=ownerless.id, user_id=member.id, now=datetime.now(timezone.utc))

    assert _role(db_session, ownerless, member) == "owner"  # Owned 2 / 1: allowed only by the operator


def test_me_reports_owned_and_allowed(client: TestClient, db_session: Session):
    user = make_user(db_session, max_owned_organizations=3)
    _owner_elsewhere(db_session, user)
    add_member(db_session, make_org(db_session, "Member only"), user, Role.VIEWER)

    body = client.get("/api/me/user", headers=_h(user)).json()

    assert (body["owned_organizations"], body["max_owned_organizations"], body["can_create_organizations"]) == (1, 3, True)


def test_the_operator_sets_the_limit_and_grant_revoke_keep_their_meaning(db_session: Session):
    user = make_user(db_session, email="limits@tests.invalid")
    _owner_elsewhere(db_session, user)

    admin.set_owned_limit(db_session, email=user.email, limit=5)
    assert db_session.get(User, user.id).max_owned_organizations == 5
    admin.set_org_creation(db_session, email=user.email, allowed=False)  # revoke: exactly what it owns now
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
