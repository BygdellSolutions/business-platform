"""Membership administration (S4): list, change role, remove, leave; authority, freshness, last owner, tenancy, events.

Rollback-only tests, run under BOTH authentication mechanisms (the `api` fixture): the dev identity and real sessions
with CSRF. The races on committed data are in test_membership_admin_concurrency.py; the database trigger is proved
here with raw SQL (`SET CONSTRAINTS ALL IMMEDIATE` fires the deferred checks inside the test's transaction).
"""

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.models import OrganizationUser, Role, SecurityEvent, User
from app.scripts import repair
from tests.auth_support import events, make_session
from tests.factories import add_member, make_org, make_user
from tests.invoicing_support import completed, draft_invoice, issue

ROLES = [r.value for r in Role]


@pytest.fixture(params=["dev", "session"])
def api(request, db_session):
    """(client, headers_for) under one of the two authentication mechanisms."""
    if request.param == "dev":
        client = request.getfixturevalue("client")

        def headers(user, org=None):
            return {"X-Dev-User-Email": user.email, **({"X-Organization-Id": str(org.id)} if org else {})}
    else:
        client = request.getfixturevalue("session_client")

        def headers(user, org=None):
            return {**make_session(db_session, user).headers, **({"X-Organization-Id": str(org.id)} if org else {})}

    return SimpleNamespace(client=client, h=headers)


class Team:
    """One organization with a member per role (and a second owner and admin)."""

    def __init__(self, db, name="Team"):
        self.db = db
        self.org = make_org(db, name)
        self.users: dict[str, User] = {}
        self.rows: dict[str, OrganizationUser] = {}
        for label, role in (("owner", "owner"), ("owner2", "owner"), ("admin", "admin"), ("admin2", "admin"), ("accountant", "accountant"), ("accountant2", "accountant"), ("employee", "employee"), ("employee2", "employee"), ("viewer", "viewer"), ("viewer2", "viewer")):
            self.add(label, role)

    def add(self, label, role, **fields):
        user = make_user(self.db, name=f"{label.title()} Person", **fields)
        self.users[label] = user
        self.rows[label] = add_member(self.db, self.org, user, Role(role))
        return user

    def role_of(self, label) -> str:
        self.db.expire_all()
        return self.db.scalar(select(OrganizationUser.role).where(OrganizationUser.id == self.rows[label].id))

    def exists(self, label) -> bool:
        return self.db.scalar(select(func.count()).select_from(OrganizationUser).where(OrganizationUser.id == self.rows[label].id)) == 1


def patch(api, team, actor, target, role, *, org=None):
    return api.client.patch(f"/api/members/{team.rows[target].id}", json={"role": role}, headers=api.h(team.users[actor], org or team.org))


def delete(api, team, actor, target, *, org=None):
    return api.client.delete(f"/api/members/{team.rows[target].id}", headers=api.h(team.users[actor], org or team.org))


def leave(api, team, actor):
    return api.client.post("/api/members/leave", headers=api.h(team.users[actor], team.org))


def code(response):
    return response.json()["detail"]["code"]


# --- the member list ----------------------------------------------------------------------------------------------------------------------


def test_owner_and_admin_list_members_with_only_what_administration_needs(api, db_session):
    team = Team(db_session)
    for actor in ("owner", "admin"):
        response = api.client.get("/api/members", headers=api.h(team.users[actor], team.org))
        assert response.status_code == 200
        members = response.json()
        assert len(members) == len(team.rows)
        assert set(members[0]) == {"id", "name", "email", "role", "is_you"}  # no user id, credential, session or capability field
        assert [m["name"] for m in members if m["is_you"]] == [team.users[actor].name]
        assert {m["id"] for m in members} == {str(r.id) for r in team.rows.values()}


@pytest.mark.parametrize("role", ["accountant", "employee", "viewer"])
def test_other_roles_cannot_list_members(api, db_session, role):
    team = Team(db_session)
    assert api.client.get("/api/members", headers=api.h(team.users[role], team.org)).status_code == 403


def test_a_non_member_keeps_the_ordinary_tenant_isolation_answer(api, db_session):
    team = Team(db_session)
    outsider = make_user(db_session)
    assert api.client.get("/api/members", headers=api.h(outsider, team.org)).status_code == 404
    assert api.client.patch(f"/api/members/{team.rows['viewer'].id}", json={"role": "viewer"}, headers=api.h(outsider, team.org)).status_code == 404
    assert api.client.delete(f"/api/members/{team.rows['viewer'].id}", headers=api.h(outsider, team.org)).status_code == 404
    assert api.client.post("/api/members/leave", headers=api.h(outsider, team.org)).status_code == 404
    assert team.exists("viewer")


# --- the authority matrix --------------------------------------------------------------------------------------------------------------------


def expected_change(actor: str, target: str, new: str) -> bool:
    """The approved matrix, restated independently of the implementation (non-self cases, owners plentiful)."""
    if actor == "owner":
        return True
    if actor == "admin":
        return target in ("accountant", "employee", "viewer") and new in ("accountant", "employee", "viewer")
    return False


CASES = [(a, t, n) for a in ("owner", "admin", "accountant", "employee", "viewer") for t in ROLES for n in ROLES if t != n]


@pytest.mark.parametrize(("actor", "target", "new"), CASES)
def test_role_change_follows_the_matrix(api, db_session, actor, target, new):
    team = Team(db_session)
    actor_label = actor
    target_label = target if target != actor else f"{target}2"  # a different person of the same role when needed
    before = team.role_of(target_label)

    response = patch(api, team, actor_label, target_label, new)

    if expected_change(actor, target, new):
        assert response.status_code == 200, response.text
        assert response.json()["role"] == new and team.role_of(target_label) == new
    else:
        assert response.status_code == 403
        assert code(response) in ("insufficient_authority", "membership_admin_forbidden")
        assert team.role_of(target_label) == before


@pytest.mark.parametrize("target", ["owner2", "admin2", "accountant", "employee", "viewer"])
def test_removal_follows_the_matrix(api, db_session, target):
    for actor in ("owner", "admin", "accountant", "employee", "viewer"):
        team = Team(db_session, f"Remove {actor} {target}")
        allowed = actor == "owner" or (actor == "admin" and target in ("accountant", "employee", "viewer"))
        response = delete(api, team, actor, target)
        assert (response.status_code == 204) is allowed, (actor, target, response.text)
        assert team.exists(target) is (not allowed)


def test_an_admin_cannot_touch_an_owner_or_another_admin_or_grant_admin_or_owner(api, db_session):
    team = Team(db_session)
    assert code(patch(api, team, "admin", "owner", "viewer")) == "insufficient_authority"
    assert code(patch(api, team, "admin", "admin2", "viewer")) == "insufficient_authority"
    assert code(patch(api, team, "admin", "employee", "admin")) == "insufficient_authority"
    assert code(patch(api, team, "admin", "employee", "owner")) == "insufficient_authority"
    assert code(delete(api, team, "admin", "owner")) == "insufficient_authority"
    assert code(delete(api, team, "admin", "admin2")) == "insufficient_authority"
    assert [team.role_of(x) for x in ("owner", "admin2", "employee")] == ["owner", "admin", "employee"]


def test_an_owner_may_promote_to_owner_and_demote_another_owner(api, db_session):
    team = Team(db_session)
    assert patch(api, team, "owner", "employee", "owner").status_code == 200
    assert patch(api, team, "owner", "owner2", "admin").status_code == 200
    assert (team.role_of("employee"), team.role_of("owner2")) == ("owner", "admin")


def test_the_request_cannot_carry_an_actor_organization_or_target_user(api, db_session):
    team = Team(db_session)
    for extra in ({"organization_id": str(uuid.uuid4())}, {"actor_user_id": str(team.users["owner"].id)}, {"actor_role": "owner"}, {"user_id": str(team.users["viewer"].id)}, {"email": "x@y.test"}):
        response = api.client.patch(f"/api/members/{team.rows['viewer'].id}", json={"role": "employee", **extra}, headers=api.h(team.users["admin"], team.org))
        assert response.status_code == 422, extra
    assert api.client.patch(f"/api/members/{team.rows['viewer'].id}", json={"role": "superuser"}, headers=api.h(team.users["admin"], team.org)).status_code == 422
    assert team.role_of("viewer") == "viewer"


# --- self actions ----------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("actor", ["admin", "accountant", "employee", "viewer"])
def test_only_an_owner_changes_their_own_role(api, db_session, actor):
    team = Team(db_session)
    for new in ROLES:
        if new == team.role_of(actor):
            continue
        response = patch(api, team, actor, actor, new)
        assert response.status_code == 403 and code(response) in ("self_role_change_not_allowed", "membership_admin_forbidden")
    assert team.role_of(actor) == actor


def test_an_owner_may_demote_themselves_while_another_owner_remains(api, db_session):
    team = Team(db_session)
    assert patch(api, team, "owner", "owner", "admin").status_code == 200
    assert team.role_of("owner") == "admin"


def test_an_owner_cannot_use_removal_to_remove_themselves(api, db_session):
    team = Team(db_session)
    response = delete(api, team, "owner", "owner")
    assert response.status_code == 403 and code(response) == "self_removal_use_leave" and team.exists("owner")
    response = delete(api, team, "admin", "admin")
    assert response.status_code == 403 and code(response) == "self_removal_use_leave" and team.exists("admin")


@pytest.mark.parametrize("who", ["owner", "admin", "accountant", "employee", "viewer"])
def test_every_member_may_leave_and_nothing_else_changes(api, db_session, who):
    team = Team(db_session)
    other_org = make_org(db_session, "Elsewhere")
    elsewhere = add_member(db_session, other_org, team.users[who], Role.EMPLOYEE)
    others = {k: v for k, v in team.rows.items() if k != who}

    response = leave(api, team, who)

    assert response.status_code == 204
    assert not team.exists(who)
    assert all(team.db.scalar(select(func.count()).select_from(OrganizationUser).where(OrganizationUser.id == row.id)) == 1 for row in others.values())
    assert db_session.get(OrganizationUser, elsewhere.id) is not None  # the other organization's membership
    assert db_session.get(User, team.users[who].id) is not None


# --- the last owner --------------------------------------------------------------------------------------------------------------


def sole_owner_team(db):
    team = Team(db)
    db.execute(text("update organization_users set role = 'admin' where id = :i"), {"i": team.rows["owner2"].id})
    settle(db)  # fire (and so clear) the deferred check of this setup demotion: it must not stand in for the check under test
    db.expire_all()
    return team


def assert_last_owner(response):
    assert response.status_code == 409 and code(response) == "last_owner"


def test_the_sole_owner_cannot_demote_themselves_or_leave(api, db_session):
    team = sole_owner_team(db_session)
    assert_last_owner(patch(api, team, "owner", "owner", "admin"))
    assert_last_owner(leave(api, team, "owner"))
    assert team.role_of("owner") == "owner" and team.exists("owner")


def test_the_sole_owner_cannot_be_demoted_or_removed_by_anyone_else(api, db_session):
    team = sole_owner_team(db_session)
    for actor in ("admin", "admin2", "employee"):
        assert patch(api, team, actor, "owner", "employee").status_code == 403
        assert delete(api, team, actor, "owner").status_code == 403
    assert delete(api, team, "owner", "owner").status_code == 403  # own removal goes through leave, which refuses with last_owner
    assert team.role_of("owner") == "owner"


def test_two_owners_allow_one_to_go_and_promotion_creates_the_second_owner_first(api, db_session):
    team = sole_owner_team(db_session)
    assert_last_owner(leave(api, team, "owner"))
    assert patch(api, team, "owner", "employee", "owner").status_code == 200  # promote first...
    assert leave(api, team, "owner").status_code == 204  # ...then the first owner may go
    assert team.role_of("employee") == "owner"
    assert_last_owner(leave(api, team, "employee"))


def test_owner_counts_are_per_organization(api, db_session):
    team = sole_owner_team(db_session)
    other = Team(db_session, "Other organization with plenty of owners")  # two owners there must not satisfy this organization
    assert other.exists("owner2")
    assert_last_owner(leave(api, team, "owner"))
    assert_last_owner(patch(api, team, "owner", "owner", "viewer"))


# --- the legacy organization without an owner --------------------------------------------------------------------------------------


def test_an_ownerless_organization_still_allows_ordinary_administration(api, db_session):
    team = Team(db_session)
    db_session.execute(text("update organization_users set role = 'admin' where organization_id = :o and role = 'owner'"), {"o": team.org.id})
    db_session.expire_all()
    assert patch(api, team, "admin", "employee", "viewer").status_code == 200
    assert delete(api, team, "admin", "viewer").status_code == 204
    assert leave(api, team, "accountant").status_code == 204


# --- freshness: the decision is made from the database, never from what was loaded earlier ------------------------------------------


def test_a_stale_loaded_actor_role_does_not_authorize(api, db_session):
    team = Team(db_session)
    assert api.client.get("/api/members", headers=api.h(team.users["admin"], team.org)).status_code == 200  # loads the admin membership (role admin)
    db_session.execute(text("update organization_users set role = 'employee' where id = :i"), {"i": team.rows["admin"].id})  # the ORM object still says admin
    assert patch(api, team, "admin", "viewer", "employee").status_code == 403
    assert delete(api, team, "admin", "viewer").status_code == 403
    assert team.exists("viewer")


def test_a_stale_loaded_target_role_does_not_authorize(api, db_session):
    team = Team(db_session)
    assert api.client.get("/api/members", headers=api.h(team.users["admin"], team.org)).status_code == 200
    db_session.execute(text("update organization_users set role = 'admin' where id = :i"), {"i": team.rows["employee"].id})  # employee became admin
    assert code(patch(api, team, "admin", "employee", "viewer")) == "insufficient_authority"
    assert code(delete(api, team, "admin", "employee")) == "insufficient_authority"


def test_a_stale_owner_count_does_not_authorize_a_departure(api, db_session):
    team = Team(db_session)
    assert api.client.get("/api/members", headers=api.h(team.users["owner"], team.org)).status_code == 200  # two owners loaded
    db_session.execute(text("update organization_users set role = 'admin' where id = :i"), {"i": team.rows["owner2"].id})
    assert_last_owner(leave(api, team, "owner"))


# --- tenancy ----------------------------------------------------------------------------------------------------------------------------


def test_another_organizations_membership_id_looks_like_a_random_id(api, db_session):
    a, b = Team(db_session, "Same Name"), Team(db_session, "Same Name")  # identical-looking organizations: same names, same roles
    foreign = b.rows["employee"]
    random_id = uuid.uuid4()

    answers = []
    for target in (foreign.id, random_id):
        patched = api.client.patch(f"/api/members/{target}", json={"role": "viewer"}, headers=api.h(a.users["owner"], a.org))
        removed = api.client.delete(f"/api/members/{target}", headers=api.h(a.users["owner"], a.org))
        answers.append((patched.status_code, patched.json(), removed.status_code, removed.json()))
    assert answers[0] == answers[1] == (404, {"detail": "Member not found"}, 404, {"detail": "Member not found"})
    assert b.role_of("employee") == "employee" and b.exists("employee")


def test_the_member_list_shows_only_this_organizations_people(api, db_session):
    a, b = Team(db_session, "Same Name"), Team(db_session, "Same Name")
    listed = api.client.get("/api/members", headers=api.h(a.users["owner"], a.org)).json()
    assert {m["id"] for m in listed} == {str(r.id) for r in a.rows.values()}
    assert not {m["id"] for m in listed} & {str(r.id) for r in b.rows.values()}
    # An owner of A cannot use B as the organization either: B is not theirs (404 like any foreign organization).
    assert api.client.get("/api/members", headers=api.h(a.users["owner"], b.org)).status_code == 404


def test_a_person_in_two_organizations_acts_with_the_role_of_the_selected_one(api, db_session):
    a, b = Team(db_session, "Role A"), Team(db_session, "Role B")
    person = make_user(db_session)
    add_member(db_session, a.org, person, Role.OWNER)
    add_member(db_session, b.org, person, Role.VIEWER)
    assert api.client.patch(f"/api/members/{b.rows['viewer'].id}", json={"role": "employee"}, headers=api.h(person, b.org)).status_code == 403
    assert api.client.patch(f"/api/members/{a.rows['viewer'].id}", json={"role": "employee"}, headers=api.h(person, a.org)).status_code == 200


# --- removal keeps the person, their history and their other memberships -----------------------------------------------------------------


def test_removal_deletes_only_the_membership_and_leaves_history_and_other_memberships(api, db_session):
    team = Team(db_session)
    victim = team.users["accountant"]
    elsewhere_org = make_org(db_session, "Other tenant")
    elsewhere = add_member(db_session, elsewhere_org, victim, Role.OWNER)
    transaction = completed(db_session, team.org)
    invoice = issue(api.client, api.h(victim, team.org), draft_invoice(api.client, api.h(victim, team.org), transaction))
    assert invoice["status"] == "issued"

    assert delete(api, team, "owner", "accountant").status_code == 204

    assert db_session.get(User, victim.id) is not None
    assert db_session.scalar(text("select issued_by from invoices where id = :i"), {"i": invoice["id"]}) == victim.id
    assert db_session.get(OrganizationUser, elsewhere.id) is not None
    assert api.client.get("/api/me/user", headers=api.h(victim)).status_code == 200  # still authenticated globally
    assert [o["name"] for o in api.client.get("/api/me/organizations", headers=api.h(victim)).json()] == ["Other tenant"]
    assert api.client.get("/api/organization", headers=api.h(victim, team.org)).status_code == 404  # ordinary tenant resolution now refuses
    assert api.client.get("/api/organization", headers=api.h(victim, elsewhere_org)).status_code == 200


def test_a_removed_persons_sessions_survive(db_session, session_client):
    team = Team(db_session)
    victim = team.users["employee"]
    handle = make_session(db_session, victim)
    owner_handle = make_session(db_session, team.users["owner"])

    response = session_client.delete(f"/api/members/{team.rows['employee'].id}", headers={**owner_handle.headers, "X-Organization-Id": str(team.org.id)})

    assert response.status_code == 204
    assert session_client.get("/api/me/user", headers=handle.read_headers).status_code == 200
    assert db_session.scalar(text("select count(*) from auth_sessions where user_id = :u and revoked_at is null"), {"u": victim.id}) == 1


def test_leaving_keeps_the_user_authenticated_and_the_organization_inaccessible(api, db_session):
    team = Team(db_session)
    assert leave(api, team, "employee").status_code == 204
    me = team.users["employee"]
    assert api.client.get("/api/me/user", headers=api.h(me)).json()["email"] == me.email
    assert api.client.get("/api/organization", headers=api.h(me, team.org)).status_code == 404
    assert api.client.get("/api/me/organizations", headers=api.h(me)).json() == []


# --- security events ----------------------------------------------------------------------------------------------------------------


def test_successful_mutations_record_one_minimal_event_each(api, db_session):
    team = Team(db_session)
    assert patch(api, team, "admin", "viewer", "employee").status_code == 200
    assert delete(api, team, "owner", "accountant").status_code == 204
    assert leave(api, team, "employee").status_code == 204

    changed, removed, left = (events(db_session, t) for t in ("member_role_changed", "member_removed", "member_left"))
    assert [(e.actor_user_id, e.organization_id, e.detail) for e in changed] == [(team.users["admin"].id, team.org.id, f"{team.users['viewer'].id} viewer>employee")]
    assert [(e.actor_user_id, e.organization_id, e.detail) for e in removed] == [(team.users["owner"].id, team.org.id, f"{team.users['accountant'].id} accountant")]
    assert [(e.actor_user_id, e.organization_id, e.detail) for e in left] == [(team.users["employee"].id, team.org.id, "employee")]
    for event in changed + removed + left:
        assert event.identifier_hash is None
        assert not any(person.email in (event.detail or "") or person.name in (event.detail or "") for person in team.users.values())


def test_refused_attempts_and_no_ops_record_no_success_event(api, db_session):
    team = Team(db_session)
    assert patch(api, team, "admin", "owner", "viewer").status_code == 403
    assert delete(api, team, "employee", "viewer").status_code == 403
    assert patch(api, team, "owner", "viewer", "viewer").status_code == 200  # nothing changed
    assert delete(api, team, "owner", "viewer").status_code == 204
    assert [e.event_type for e in events(db_session) if e.event_type.startswith("member_")] == ["member_removed"]
    sole = sole_owner_team(db_session)
    assert_last_owner(leave(api, sole, "owner"))
    assert not [e for e in events(db_session, "member_left")]


# --- the database backstop (raw SQL, bypassing the application) -----------------------------------------------------------------------


def settle(db):
    """Fire the deferred owner checks now, then go back to deferring them (as at a real COMMIT)."""
    db.execute(text("set constraints all immediate"))
    db.execute(text("set constraints all deferred"))


def refused(db, sql, **params):
    with pytest.raises(IntegrityError, match="organization_owner_required|without an owner"):
        with db.begin_nested():
            db.execute(text(sql), params)
            settle(db)


def test_raw_sql_cannot_delete_or_demote_the_sole_owner_or_move_them_away(db_session):
    team = sole_owner_team(db_session)
    owner, other_org = team.rows["owner"].id, make_org(db_session, "Elsewhere").id
    refused(db_session, "delete from organization_users where id = :i", i=owner)
    refused(db_session, "update organization_users set role = 'admin' where id = :i", i=owner)
    refused(db_session, "update organization_users set role = 'viewer' where id = :i", i=owner)
    refused(db_session, "update organization_users set organization_id = :o where id = :i", i=owner, o=other_org)
    assert team.role_of("owner") == "owner"


def test_with_two_owners_losing_one_is_allowed_and_a_swap_in_one_transaction_is_allowed(db_session):
    team = Team(db_session)
    db_session.execute(text("delete from organization_users where id = :i"), {"i": team.rows["owner2"].id})
    settle(db_session)  # one owner remains
    refused(db_session, "update organization_users set role = 'admin' where id = :i", i=team.rows["owner"].id)

    # demote the last owner and promote someone else in the SAME transaction: deferral makes this legal
    db_session.execute(text("update organization_users set role = 'admin' where id = :i"), {"i": team.rows["owner"].id})
    db_session.execute(text("update organization_users set role = 'owner' where id = :i"), {"i": team.rows["employee"].id})
    settle(db_session)
    assert team.role_of("employee") == "owner"


def test_a_legacy_ownerless_organization_stays_valid_and_unrelated_changes_are_unaffected(db_session):
    org = make_org(db_session, "Legacy")
    admin, employee = make_user(db_session), make_user(db_session)
    a, e = add_member(db_session, org, admin, Role.ADMIN), add_member(db_session, org, employee, Role.EMPLOYEE)
    settle(db_session)
    db_session.execute(text("update organization_users set role = 'viewer' where id = :i"), {"i": e.id})
    db_session.execute(text("update organization_users set role = 'accountant' where id = :i"), {"i": a.id})
    db_session.execute(text("delete from organization_users where id = :i"), {"i": e.id})
    settle(db_session)
    assert db_session.scalar(select(func.count()).select_from(OrganizationUser).where(OrganizationUser.organization_id == org.id)) == 1


def test_the_backstop_counts_owners_per_organization_and_ignores_unrelated_organizations(db_session):
    a, b = sole_owner_team(db_session), Team(db_session, "Plenty of owners")
    refused(db_session, "delete from organization_users where id = :i", i=a.rows["owner"].id)  # B's owners do not count for A
    db_session.execute(text("delete from organization_users where id = :i"), {"i": b.rows["employee"].id})
    settle(db_session)


def test_creating_an_organization_and_its_first_owner_is_not_the_triggers_business(db_session):
    org = make_org(db_session, "Brand new")
    user = make_user(db_session)
    add_member(db_session, org, user, Role.OWNER)  # inserts are not checked
    settle(db_session)
    assert db_session.scalar(select(func.count()).select_from(OrganizationUser).where(OrganizationUser.organization_id == org.id)) == 1


# --- the operator repair --------------------------------------------------------------------------------------------------------------


def legacy(db):
    org = make_org(db, "Legacy ownerless")
    member = make_user(db)
    add_member(db, org, member, Role.ADMIN)
    return org, member


def test_the_operator_repairs_an_ownerless_organization_by_promoting_an_existing_member(db_session):
    org, member = legacy(db_session)
    repair.repair_owner(db_session, organization_id=org.id, email=member.email.upper())

    assert db_session.scalar(select(OrganizationUser.role).where(OrganizationUser.organization_id == org.id, OrganizationUser.user_id == member.id)) == "owner"
    recorded = events(db_session, "owner_repaired")
    assert [(e.actor_user_id, e.organization_id, e.detail) for e in recorded] == [(member.id, org.id, "cli admin>owner")]


def test_the_repair_refuses_an_organization_with_an_owner_a_non_member_an_unknown_organization_and_unknown_user(db_session):
    org, member = legacy(db_session)
    outsider = make_user(db_session)
    with pytest.raises(repair.RepairError, match="not a member"):
        repair.repair_owner(db_session, organization_id=org.id, email=outsider.email)
    assert db_session.scalar(select(func.count()).select_from(OrganizationUser).where(OrganizationUser.user_id == outsider.id)) == 0  # no membership is created
    with pytest.raises(repair.RepairError, match="no organization"):
        repair.repair_owner(db_session, organization_id=uuid.uuid4(), email=member.email)
    with pytest.raises(repair.RepairError, match="no user"):
        repair.repair_owner(db_session, organization_id=org.id, email="nobody@tests.invalid")
    repair.repair_owner(db_session, organization_id=org.id, email=member.email)
    with pytest.raises(repair.RepairError, match="already has an owner"):
        repair.repair_owner(db_session, organization_id=org.id, email=member.email)
    assert len(events(db_session, "owner_repaired")) == 1


def test_the_repair_never_touches_another_organization(db_session):
    org, member = legacy(db_session)
    other, other_member = legacy(db_session)
    repair.repair_owner(db_session, organization_id=org.id, email=member.email)
    assert db_session.scalar(select(OrganizationUser.role).where(OrganizationUser.organization_id == other.id)) == "admin"
