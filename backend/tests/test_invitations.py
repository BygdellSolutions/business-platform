"""Organization invitations (S5): administration, the token, existing-account and new-account acceptance, tenancy, events.

Rollback-only tests (one shared session). The administration and existing-account acceptance run under BOTH
authentication mechanisms (the `api` fixture); creating an account is session mode only. Races, committed-state
proofs and the atomic new-account failure are in test_invitations_concurrency.py.
"""

import re
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text

from app.core import clock
from app.core.tokens import hash_token
from app.models import OrganizationInvitation, OrganizationUser, Role, SecurityEvent, User, UserCredential
from tests.auth_support import events, make_session
from tests.factories import add_member, make_org, make_user

ROLES = [r.value for r in Role]
INVITE = "/api/invitations"


@pytest.fixture(params=["dev", "session"])
def api(request, db_session):
    if request.param == "dev":
        client = request.getfixturevalue("client")

        def headers(user, org=None):
            return {"X-Dev-User-Email": user.email, **({"X-Organization-Id": str(org.id)} if org else {})}
    else:
        client = request.getfixturevalue("session_client")

        def headers(user, org=None):
            return {**make_session(db_session, user).headers, **({"X-Organization-Id": str(org.id)} if org else {})}

    return SimpleNamespace(client=client, h=headers, mode=request.param)


class Team:
    def __init__(self, db, name="Invite Team"):
        self.db, self.org, self.users, self.rows = db, make_org(db, name), {}, {}
        for label, role in (("owner", "owner"), ("owner2", "owner"), ("admin", "admin"), ("admin2", "admin"), ("accountant", "accountant"), ("employee", "employee"), ("viewer", "viewer")):
            user = make_user(db, name=f"{label.title()} Person")
            self.users[label], self.rows[label] = user, add_member(db, self.org, user, Role(role))


def email() -> str:
    return f"{uuid.uuid4().hex[:10]}@invitees.invalid"


def invite(api, team, actor, to=None, role="employee", *, org=None):
    return api.client.post(INVITE, json={"email": to or email(), "role": role}, headers=api.h(team.users[actor], org or team.org))


def listing(api, team, actor):
    return api.client.get(INVITE, headers=api.h(team.users[actor], team.org))


def preview(api, token):
    return api.client.post("/api/invite/preview", json={"token": token})


def accept(api, user, token, *, org=None):
    return api.client.post("/api/invite/accept", json={"token": token}, headers=api.h(user, org))


def code(response):
    return response.json()["detail"]["code"]


def role_in(db, org, user):
    db.expire_all()
    return db.scalar(select(OrganizationUser.role).where(OrganizationUser.organization_id == org.id, OrganizationUser.user_id == user.id))


def count(db, model, *conditions):
    return db.scalar(select(func.count()).select_from(model).where(*conditions))


# --- administration ------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ROLES)
def test_an_owner_invites_any_role_and_the_secret_is_returned_once(api, db_session, role):
    team = Team(db_session)
    to = email()

    created = invite(api, team, "owner", to, role)

    assert created.status_code == 201, created.text
    body = created.json()
    assert set(body) == {"id", "email", "role", "created_at", "expires_at", "state", "token"}
    assert body["email"] == to and body["role"] == role and body["state"] == "pending"
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", body["token"])  # 256 random bits, url-safe
    listed = listing(api, team, "owner").json()
    assert [row["id"] for row in listed] == [body["id"]]
    assert set(listed[0]) == {"id", "email", "role", "created_at", "expires_at", "state"}  # never the token or its hash


@pytest.mark.parametrize("role", ["accountant", "employee", "viewer"])
def test_an_admin_invites_ordinary_roles(api, db_session, role):
    assert invite(api, Team(db_session), "admin", role=role).status_code == 201


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_an_admin_cannot_invite_an_owner_or_an_admin(api, db_session, role):
    team = Team(db_session)
    response = invite(api, team, "admin", role=role)
    assert response.status_code == 403 and code(response) == "insufficient_authority"
    assert count(db_session, OrganizationInvitation) == 0


@pytest.mark.parametrize("actor", ["accountant", "employee", "viewer"])
def test_ordinary_roles_have_no_invitation_administration(api, db_session, actor):
    team = Team(db_session)
    made = invite(api, team, "owner", role="viewer").json()
    assert invite(api, team, actor, role="viewer").status_code == 403
    assert listing(api, team, actor).status_code == 403
    assert api.client.delete(f"{INVITE}/{made['id']}", headers=api.h(team.users[actor], team.org)).status_code == 403
    assert api.client.post(f"{INVITE}/{made['id']}/regenerate", headers=api.h(team.users[actor], team.org)).status_code == 403
    assert count(db_session, OrganizationInvitation, OrganizationInvitation.revoked_at.is_not(None)) == 0


def test_revocation_follows_the_same_authority_as_membership_administration(api, db_session):
    team = Team(db_session)
    for role in ("owner", "admin", "accountant"):
        made = invite(api, team, "owner", role=role).json()
        admin_attempt = api.client.delete(f"{INVITE}/{made['id']}", headers=api.h(team.users["admin"], team.org))
        if role in ("owner", "admin"):
            assert admin_attempt.status_code == 403 and code(admin_attempt) == "insufficient_authority"
            assert api.client.delete(f"{INVITE}/{made['id']}", headers=api.h(team.users["owner"], team.org)).status_code == 204
        else:
            assert admin_attempt.status_code == 204
    assert listing(api, team, "owner").json() == []  # revoked invitations leave the list


def test_revoking_is_idempotent_never_deletes_and_cannot_undo_an_acceptance(api, db_session):
    team = Team(db_session)
    made = invite(api, team, "owner", role="viewer").json()
    assert api.client.delete(f"{INVITE}/{made['id']}", headers=api.h(team.users["owner"], team.org)).status_code == 204
    assert api.client.delete(f"{INVITE}/{made['id']}", headers=api.h(team.users["owner"], team.org)).status_code == 204
    row = db_session.get(OrganizationInvitation, uuid.UUID(made["id"]))
    assert row is not None and row.revoked_at is not None
    assert len(events(db_session, "invitation_revoked")) == 1  # the second revoke changed nothing

    person = make_user(db_session)
    accepted = invite(api, team, "owner", person.email, "viewer").json()
    assert accept(api, person, accepted["token"]).status_code == 200
    response = api.client.delete(f"{INVITE}/{accepted['id']}", headers=api.h(team.users["owner"], team.org))
    assert response.status_code == 409 and code(response) == "invitation_accepted"
    assert role_in(db_session, team.org, person) == "viewer"  # revoking never removes a membership


def test_revoking_never_removes_a_membership_even_when_the_person_joined_another_way(api, db_session):
    team = Team(db_session)
    person = make_user(db_session)
    made = invite(api, team, "owner", person.email, "admin").json()
    add_member(db_session, team.org, person, Role.VIEWER)  # joined another way while the invitation was pending

    assert api.client.delete(f"{INVITE}/{made['id']}", headers=api.h(team.users["owner"], team.org)).status_code == 204

    assert role_in(db_session, team.org, person) == "viewer"
    assert count(db_session, User, User.id == person.id) == 1


def test_regeneration_issues_a_new_token_and_retires_the_old_one(api, db_session):
    team = Team(db_session)
    first = invite(api, team, "owner", role="viewer").json()

    second = api.client.post(f"{INVITE}/{first['id']}/regenerate", headers=api.h(team.users["owner"], team.org))

    assert second.status_code == 201
    new = second.json()
    assert new["token"] != first["token"] and new["id"] != first["id"] and new["email"] == first["email"] and new["role"] == "viewer"
    assert preview(api, first["token"]).status_code == 404 and preview(api, new["token"]).status_code == 200
    assert [row["id"] for row in listing(api, team, "owner").json()] == [new["id"]]
    assert api.client.post(f"{INVITE}/{first['id']}/regenerate", headers=api.h(team.users["owner"], team.org)).status_code == 409  # already replaced


def test_an_admin_cannot_regenerate_an_owner_or_admin_invitation(api, db_session):
    team = Team(db_session)
    made = invite(api, team, "owner", role="admin").json()
    assert api.client.post(f"{INVITE}/{made['id']}/regenerate", headers=api.h(team.users["admin"], team.org)).status_code == 403
    assert preview(api, made["token"]).status_code == 200  # untouched


def test_inviting_an_existing_member_is_refused_and_the_email_is_normalized(api, db_session):
    team = Team(db_session)
    member = team.users["viewer"]
    response = invite(api, team, "owner", f"  {member.email.upper()} ", "admin")
    assert response.status_code == 409 and code(response) == "already_member"

    created = invite(api, team, "owner", "  Mixed.Case@Invitees.INVALID ", "viewer").json()
    assert created["email"] == "mixed.case@invitees.invalid"
    assert db_session.scalar(select(OrganizationInvitation.email).where(OrganizationInvitation.id == uuid.UUID(created["id"]))) == "mixed.case@invitees.invalid"


def test_one_pending_invitation_per_email_and_an_expired_one_is_superseded_explicitly(api, db_session):
    team = Team(db_session)
    to = email()
    first = invite(api, team, "owner", to, "viewer").json()
    again = invite(api, team, "admin", to.upper(), "employee")
    assert again.status_code == 409 and code(again) == "invitation_pending"

    db_session.execute(text("update organization_invitations set expires_at = now() - interval '1 hour' where id = :i"), {"i": first["id"]})
    db_session.expire_all()  # the raw update bypasses the ORM: reload
    assert [row["state"] for row in listing(api, team, "owner").json()] == ["expired"]

    replacement = invite(api, team, "owner", to, "employee")
    assert replacement.status_code == 201
    assert db_session.get(OrganizationInvitation, uuid.UUID(first["id"])).revoked_at is not None
    assert [(row["id"], row["state"]) for row in listing(api, team, "owner").json()] == [(replacement.json()["id"], "pending")]
    assert [e.detail.split()[1] for e in events(db_session, "invitation_revoked")] == ["superseded"]
    assert preview(api, first["token"]).status_code == 404  # the expired token stays dead


def test_the_database_allows_one_pending_invitation_per_organization_and_email(db_session):
    team = Team(db_session)
    other_org = make_org(db_session, "Other")
    base = dict(email="x@invitees.invalid", role="viewer", expires_at=clock.utcnow() + timedelta(days=1), created_by=team.users["owner"].id)
    from sqlalchemy.exc import IntegrityError

    db_session.add(OrganizationInvitation(organization_id=team.org.id, token_hash=hash_token("a" * 43), **base))
    db_session.flush()
    db_session.add(OrganizationInvitation(organization_id=other_org.id, token_hash=hash_token("b" * 43), **base))  # another organization: fine
    db_session.flush()
    for bad in (
        OrganizationInvitation(organization_id=team.org.id, token_hash=hash_token("c" * 43), **base),  # a second pending one
        OrganizationInvitation(organization_id=team.org.id, token_hash=hash_token("a" * 43), **{**base, "email": "y@invitees.invalid"}),  # a repeated token hash
        OrganizationInvitation(organization_id=team.org.id, token_hash="not a hash", **{**base, "email": "z@invitees.invalid"}),
        OrganizationInvitation(organization_id=team.org.id, token_hash=hash_token("d" * 43), **{**base, "email": "Upper@invitees.invalid"}),
    ):
        with pytest.raises(IntegrityError):
            with db_session.begin_nested():
                db_session.add(bad)
                db_session.flush()


def test_the_request_cannot_carry_an_organization_inviter_token_or_acceptor(api, db_session):
    team = Team(db_session)
    for extra in ({"organization_id": str(uuid.uuid4())}, {"created_by": str(team.users["owner"].id)}, {"token": "x" * 43}, {"accepted_by": str(uuid.uuid4())}, {"actor_role": "owner"}):
        response = api.client.post(INVITE, json={"email": email(), "role": "viewer", **extra}, headers=api.h(team.users["owner"], team.org))
        assert response.status_code == 422, extra
    assert api.client.post(INVITE, json={"email": "not an email", "role": "viewer"}, headers=api.h(team.users["owner"], team.org)).status_code == 422
    assert count(db_session, OrganizationInvitation) == 0


def test_authority_is_decided_from_the_database_not_from_a_stale_loaded_role(api, db_session):
    team = Team(db_session)
    assert listing(api, team, "admin").status_code == 200  # loads the admin membership
    db_session.execute(text("update organization_users set role = 'employee' where id = :i"), {"i": team.rows["admin"].id})  # the loaded object still says admin
    assert invite(api, team, "admin", role="viewer").status_code == 403
    assert count(db_session, OrganizationInvitation) == 0


# --- tenancy ------------------------------------------------------------------------------------------------------------------


def test_a_foreign_invitation_id_looks_like_a_random_one_and_nothing_there_changes(api, db_session):
    a, b = Team(db_session, "Same Name"), Team(db_session, "Same Name")
    foreign = invite(api, b, "owner", "same@invitees.invalid", "viewer").json()
    mine = invite(api, a, "owner", "same@invitees.invalid", "viewer").json()  # an identical-looking invitation in the other organization

    answers = []
    for target in (foreign["id"], str(uuid.uuid4())):
        deleted = api.client.delete(f"{INVITE}/{target}", headers=api.h(a.users["owner"], a.org))
        regenerated = api.client.post(f"{INVITE}/{target}/regenerate", headers=api.h(a.users["owner"], a.org))
        answers.append((deleted.status_code, deleted.json(), regenerated.status_code, regenerated.json()))
    assert answers[0] == answers[1] == (404, {"detail": "Invitation not found"}, 404, {"detail": "Invitation not found"})
    assert preview(api, foreign["token"]).status_code == 200 and preview(api, mine["token"]).status_code == 200
    assert [row["id"] for row in listing(api, a, "owner").json()] == [mine["id"]]
    assert api.client.get(INVITE, headers=api.h(a.users["owner"], b.org)).status_code == 404  # not their organization
    assert api.client.post(INVITE, json={"email": email(), "role": "viewer"}, headers=api.h(make_user(db_session), a.org)).status_code == 404


# --- the preview ---------------------------------------------------------------------------------------------------------------


def test_the_preview_shows_only_what_the_acceptance_screen_needs(api, db_session):
    team = Team(db_session, "Preview Org")
    to = email()
    token = invite(api, team, "owner", to, "accountant").json()["token"]

    response = preview(api, token)

    assert response.status_code == 200
    assert response.json() == {"organization_name": "Preview Org", "email": to, "role": "accountant", "account_exists": False}
    make_user(db_session, email=to)
    assert preview(api, token).json()["account_exists"] is True  # the holder may learn this (the invitation is bound to that email)


def test_random_revoked_expired_and_used_tokens_are_indistinguishable(api, db_session):
    team = Team(db_session)
    person = make_user(db_session)
    revoked = invite(api, team, "owner", role="viewer").json()
    api.client.delete(f"{INVITE}/{revoked['id']}", headers=api.h(team.users["owner"], team.org))
    expired = invite(api, team, "owner", role="viewer").json()
    db_session.execute(text("update organization_invitations set expires_at = now() - interval '1 second' where id = :i"), {"i": expired["id"]})
    db_session.expire_all()  # the raw update bypasses the ORM: reload
    used = invite(api, team, "owner", person.email, "viewer").json()
    accept(api, person, used["token"])

    bodies = []
    for token in ("A" * 43, "short", revoked["token"], expired["token"], used["token"]):
        response = preview(api, token)
        bodies.append((response.status_code, response.json()))
    assert set(map(str, bodies)) == {str((404, {"detail": "Invitation not found"}))}


def test_a_token_in_a_url_is_never_accepted(api, db_session):
    team = Team(db_session)
    token = invite(api, team, "owner", role="viewer").json()["token"]
    for path in (f"/api/invite/preview?token={token}", f"/api/invite/preview/{token}", f"/api/invite/accept?token={token}"):
        assert api.client.get(path).status_code in (404, 405, 401)
    assert api.client.post(f"/api/invite/preview?token={token}", json={}).status_code == 422  # the body is where it must be


# --- the token is stored hashed and appears nowhere else ---------------------------------------------------------------------------


def test_only_the_hash_is_stored_and_the_secret_is_in_no_event_or_column(api, db_session):
    team = Team(db_session)
    made = invite(api, team, "owner", role="viewer").json()
    token = made["token"]
    row = db_session.get(OrganizationInvitation, uuid.UUID(made["id"]))
    assert row.token_hash == hash_token(token) and row.token_hash != token

    tables = ("organization_invitations", "security_events", "organization_users", "users")
    for table in tables:
        cells = db_session.execute(text(f"select row_to_json(t)::text from {table} t")).scalars().all()
        assert not any(token in cell for cell in cells), table
    for event in events(db_session):
        assert token not in (event.detail or "") and row.token_hash not in (event.detail or "")


def test_every_token_is_different(api, db_session):
    team = Team(db_session)
    tokens = {invite(api, team, "owner", role="viewer").json()["token"] for _ in range(20)}
    assert len(tokens) == 20


# --- accepting as an existing account -------------------------------------------------------------------------------------------


def test_a_matching_account_accepts_and_gets_exactly_the_invited_role(api, db_session):
    team = Team(db_session)
    person = make_user(db_session)
    token = invite(api, team, "admin", person.email.upper(), "accountant").json()["token"]

    response = accept(api, person, token)

    assert response.status_code == 200
    assert response.json() == {"organization_id": str(team.org.id), "role": "accountant", "joined": True}
    assert role_in(db_session, team.org, person) == "accountant"
    scoped = {**api.h(person), "X-Organization-Id": str(team.org.id)}
    assert api.client.get("/api/organization", headers=scoped).status_code == 200  # the ordinary tenant path takes over


def test_a_wrong_account_is_refused_and_the_invitation_stays_usable(api, db_session):
    team = Team(db_session)
    invited, other = make_user(db_session), make_user(db_session)
    token = invite(api, team, "owner", invited.email, "viewer").json()["token"]

    refused = accept(api, other, token)

    assert refused.status_code == 403 and code(refused) == "invitation_wrong_account"
    assert role_in(db_session, team.org, other) is None
    assert preview(api, token).status_code == 200  # nothing was consumed
    assert accept(api, invited, token).status_code == 200


def test_the_organization_comes_from_the_invitation_and_the_token_is_not_a_tenant_selector(api, db_session):
    team, elsewhere = Team(db_session, "Real"), Team(db_session, "Decoy")
    person = make_user(db_session)
    token = invite(api, team, "owner", person.email, "viewer").json()["token"]

    response = accept(api, person, token, org=elsewhere.org)  # a hostile selector header is irrelevant

    assert response.status_code == 200 and response.json()["organization_id"] == str(team.org.id)
    assert role_in(db_session, elsewhere.org, person) is None
    for body in ({"token": token, "organization_id": str(elsewhere.org.id)}, {"token": token, "role": "owner"}):
        assert api.client.post("/api/invite/accept", json=body, headers=api.h(person)).status_code == 422  # not browser-controlled


def test_an_invitation_never_changes_an_existing_membership_role(api, db_session):
    team = Team(db_session)
    person = make_user(db_session)
    token = invite(api, team, "owner", person.email, "admin").json()["token"]
    add_member(db_session, team.org, person, Role.VIEWER)  # joined by another path meanwhile

    response = accept(api, person, token)

    assert response.status_code == 200 and response.json() == {"organization_id": str(team.org.id), "role": "viewer", "joined": False}
    assert role_in(db_session, team.org, person) == "viewer"  # a stale admin invitation does not promote a viewer
    assert count(db_session, OrganizationUser, OrganizationUser.user_id == person.id) == 1
    assert db_session.scalar(select(OrganizationInvitation.accepted_by).where(OrganizationInvitation.token_hash == hash_token(token))) == person.id  # settled
    assert [e.detail.split()[1] for e in events(db_session, "invitation_accepted")] == ["existing"]


def test_an_invitation_is_single_use_and_a_retry_by_the_same_person_is_idempotent(api, db_session):
    team = Team(db_session)
    person, other = make_user(db_session), make_user(db_session)
    token = invite(api, team, "owner", person.email, "employee").json()["token"]
    assert accept(api, person, token).json()["joined"] is True

    retry = accept(api, person, token)
    assert retry.status_code == 200 and retry.json() == {"organization_id": str(team.org.id), "role": "employee", "joined": False}
    assert count(db_session, OrganizationUser, OrganizationUser.user_id == person.id) == 1 and len(events(db_session, "invitation_accepted")) == 1

    stranger = accept(api, other, token)
    assert stranger.status_code == 404 and stranger.json() == {"detail": "Invitation not found"}
    assert role_in(db_session, team.org, other) is None

    db_session.execute(text("delete from organization_users where user_id = :u"), {"u": person.id})
    assert accept(api, person, token).status_code == 404  # no longer a member: the used token grants nothing


def test_revoked_and_expired_invitations_cannot_be_accepted(api, db_session):
    team = Team(db_session)
    person = make_user(db_session)
    revoked = invite(api, team, "owner", person.email, "viewer").json()
    api.client.delete(f"{INVITE}/{revoked['id']}", headers=api.h(team.users["owner"], team.org))
    assert accept(api, person, revoked["token"]).status_code == 404

    expired = invite(api, team, "owner", person.email, "viewer").json()
    db_session.execute(text("update organization_invitations set expires_at = now() - interval '1 second' where id = :i"), {"i": expired["id"]})
    db_session.expire_all()  # the raw update bypasses the ORM: reload
    assert accept(api, person, expired["token"]).status_code == 404
    assert accept(api, person, "A" * 43).status_code == 404 and role_in(db_session, team.org, person) is None


def test_accepting_needs_authentication(api, db_session):
    team = Team(db_session)
    token = invite(api, team, "owner", role="viewer").json()["token"]
    assert api.client.post("/api/invite/accept", json={"token": token}).status_code == 401


def test_an_owner_invitation_adds_an_owner_and_the_last_owner_trigger_is_untouched(api, db_session):
    team = Team(db_session)
    person = make_user(db_session)
    token = invite(api, team, "owner", person.email, "owner").json()["token"]
    assert accept(api, person, token).json()["role"] == "owner"
    db_session.execute(text("set constraints all immediate"))  # the owner-loss triggers still exist and are satisfied
    assert count(db_session, OrganizationUser, OrganizationUser.organization_id == team.org.id, OrganizationUser.role == "owner") == 3


def test_the_legacy_ownerless_organization_gets_no_special_path(api, db_session):
    org = make_org(db_session, "Legacy")
    admin = make_user(db_session)
    add_member(db_session, org, admin, Role.ADMIN)
    team = SimpleNamespace(org=org, users={"admin": admin})
    assert invite(api, team, "admin", role="owner").status_code == 403  # an admin still cannot invite an owner; the operator repairs it


# --- events -----------------------------------------------------------------------------------------------------------------------


def test_events_record_ids_and_roles_only(api, db_session):
    team = Team(db_session)
    person = make_user(db_session)
    made = invite(api, team, "admin", person.email, "viewer").json()
    accept(api, person, made["token"])
    other = invite(api, team, "owner", role="employee").json()
    api.client.delete(f"{INVITE}/{other['id']}", headers=api.h(team.users["owner"], team.org))

    created, accepted, revoked = (events(db_session, t) for t in ("invitation_created", "invitation_accepted", "invitation_revoked"))
    assert [(e.actor_user_id, e.organization_id, e.detail) for e in created][0] == (team.users["admin"].id, team.org.id, f"{made['id']} viewer")
    assert [(e.actor_user_id, e.organization_id, e.detail) for e in accepted] == [(person.id, team.org.id, f"{made['id']} viewer")]
    assert [(e.actor_user_id, e.detail) for e in revoked] == [(team.users["owner"].id, f"{other['id']} employee")]
    for event in created + accepted + revoked:
        assert event.identifier_hash is None
        assert made["token"] not in (event.detail or "") and person.email not in (event.detail or "")


def test_refused_requests_record_no_event(api, db_session):
    team = Team(db_session)
    invite(api, team, "admin", role="owner")
    invite(api, team, "viewer", role="viewer")
    accept(api, make_user(db_session), "A" * 43)
    assert [e for e in events(db_session) if e.event_type.startswith("invitation_")] == []


# --- creating an account through an invitation (session mode only) ----------------------------------------------------------------------


PASSWORD = "correct horse battery staple"


def accept_new(client, token, name="New Person", password=PASSWORD, **extra):
    return client.post("/api/invite/accept-new", json={"token": token, "name": name, "password": password, **extra})


@pytest.fixture
def session_team(db_session, session_client):
    team = Team(db_session)
    owner = make_session(db_session, team.users["owner"])

    def make_invitation(to=None, role="employee"):
        response = session_client.post(INVITE, json={"email": to or email(), "role": role}, headers={**owner.headers, "X-Organization-Id": str(team.org.id)})
        assert response.status_code == 201, response.text
        return response.json()

    return SimpleNamespace(team=team, invite=make_invitation, client=session_client)


def test_a_new_account_is_created_with_the_invited_email_a_membership_and_a_session(session_team, db_session):
    made = session_team.invite("brand.new@invitees.invalid", "accountant")

    response = accept_new(session_team.client, made["token"], name="  Nina New  ")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["email"] == "brand.new@invitees.invalid" and body["user"]["name"] == "Nina New" and body["user"]["can_create_organizations"] is False
    assert body["organization_id"] == str(session_team.team.org.id) and body["role"] == "accountant"
    user = db_session.scalar(select(User).where(User.email == "brand.new@invitees.invalid"))
    assert count(db_session, UserCredential, UserCredential.user_id == user.id) == 1
    assert role_in(db_session, session_team.team.org, user) == "accountant"
    assert db_session.scalar(select(OrganizationInvitation.accepted_by).where(OrganizationInvitation.id == uuid.UUID(made["id"]))) == user.id
    # the session works, and the ordinary tenant path takes over
    headers = {"Authorization": f"Bearer {body['token']}", "X-Organization-Id": body["organization_id"]}
    assert session_team.client.get("/api/me", headers=headers).json()["role"] == "accountant"


def test_the_browser_cannot_choose_the_email_or_the_organization_or_the_role(session_team, db_session):
    made = session_team.invite("fixed@invitees.invalid", "viewer")
    for extra in ({"email": "someone.else@invitees.invalid"}, {"organization_id": str(uuid.uuid4())}, {"role": "owner"}):
        assert accept_new(session_team.client, made["token"], **extra).status_code == 422, extra
    assert db_session.scalar(select(User.id).where(User.email == "someone.else@invitees.invalid")) is None
    assert db_session.scalar(select(User.id).where(User.email == "fixed@invitees.invalid")) is None  # nothing was created at all


def test_a_weak_password_consumes_nothing_and_the_invitee_can_retry(session_team, db_session):
    to = "weak@invitees.invalid"
    made = session_team.invite(to, "viewer")
    users_before = count(db_session, User)

    weak = accept_new(session_team.client, made["token"], password="short")

    assert weak.status_code == 422 and weak.json()["detail"]["code"] == "password_policy"
    assert accept_new(session_team.client, made["token"], password=to).status_code == 422  # the email itself is not a password
    assert count(db_session, User) == users_before and db_session.scalar(select(User.id).where(User.email == to)) is None
    assert session_team.client.post("/api/invite/preview", json={"token": made["token"]}).status_code == 200  # still usable
    assert accept_new(session_team.client, made["token"]).status_code == 200


def test_an_existing_account_must_sign_in_instead_and_nothing_is_consumed(session_team, db_session):
    existing = make_user(db_session)
    made = session_team.invite(existing.email, "viewer")

    response = accept_new(session_team.client, made["token"])

    assert response.status_code == 409 and response.json()["detail"]["code"] == "account_exists"
    assert db_session.scalar(select(UserCredential.user_id).where(UserCredential.user_id == existing.id)) is None  # the existing account gained no password
    assert session_team.client.post("/api/invite/preview", json={"token": made["token"]}).json()["account_exists"] is True


def test_an_account_is_created_once_and_a_retry_gets_the_generic_answer(session_team, db_session):
    made = session_team.invite("once@invitees.invalid", "viewer")
    assert accept_new(session_team.client, made["token"]).status_code == 200

    retry = accept_new(session_team.client, made["token"], password="another long passphrase")

    assert retry.status_code == 404 and retry.json() == {"detail": "Invitation not found"}  # no second session, no password reset
    user = db_session.scalar(select(User).where(User.email == "once@invitees.invalid"))
    assert count(db_session, User, User.email == "once@invitees.invalid") == 1 and count(db_session, UserCredential, UserCredential.user_id == user.id) == 1


def test_creating_an_account_with_an_unusable_token_is_the_generic_answer(session_team, db_session):
    made = session_team.invite("gone@invitees.invalid", "viewer")
    owner = make_session(db_session, session_team.team.users["owner"])
    session_team.client.delete(f"{INVITE}/{made['id']}", headers={**owner.headers, "X-Organization-Id": str(session_team.team.org.id)})
    for token in (made["token"], "A" * 43, "short"):
        response = accept_new(session_team.client, token)
        assert response.status_code == 404 and response.json() == {"detail": "Invitation not found"}
    assert db_session.scalar(select(User.id).where(User.email == "gone@invitees.invalid")) is None


def test_creating_an_account_does_not_exist_outside_session_mode(client, db_session):
    assert client.post("/api/invite/accept-new", json={"token": "A" * 43, "name": "X", "password": PASSWORD}).status_code == 404


def test_account_creation_event_has_the_new_user_and_no_secret(session_team, db_session):
    made = session_team.invite("evt@invitees.invalid", "viewer")
    accept_new(session_team.client, made["token"], password=PASSWORD)
    accepted = events(db_session, "invitation_accepted")
    assert len(accepted) == 1 and accepted[0].detail == f"{made['id']} viewer"
    assert accepted[0].actor_user_id == db_session.scalar(select(User.id).where(User.email == "evt@invitees.invalid"))
    password_hash = db_session.scalar(select(UserCredential.password_hash).where(UserCredential.user_id == accepted[0].actor_user_id))
    for event in db_session.scalars(select(SecurityEvent)):
        assert PASSWORD not in (event.detail or "") and made["token"] not in (event.detail or "")
        assert password_hash[:20] not in (event.detail or "") and "argon2" not in (event.detail or "")


def test_ordinary_session_csrf_binds_the_authenticated_acceptance(db_session, session_client):
    team = Team(db_session)
    person = make_user(db_session)
    owner = make_session(db_session, team.users["owner"])
    token = session_client.post(INVITE, json={"email": person.email, "role": "viewer"}, headers={**owner.headers, "X-Organization-Id": str(team.org.id)}).json()["token"]
    handle = make_session(db_session, person)

    assert session_client.post("/api/invite/accept", json={"token": token}, headers=handle.read_headers).status_code == 403  # no CSRF token
    assert session_client.post("/api/invite/accept", json={"token": token}, headers={**handle.read_headers, "X-CSRF-Token": "x" * 43}).status_code == 403
    assert role_in(db_session, team.org, person) is None
    assert session_client.post("/api/invite/accept", json={"token": token}, headers=handle.headers).status_code == 200
