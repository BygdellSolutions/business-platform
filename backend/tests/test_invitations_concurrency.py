"""Invitations under real concurrency: committed data, separate connections, genuine waiting (session mode).

What is proved from the COMMITTED state (not just response codes): at most one membership and one acceptance per
invitation, one user per normalized email, no role escalation, no stale-admin authority, no token reuse, nothing
left behind by a failed new-account acceptance, and no deadlock with membership administration (the combined lock
order is memberships -> invitation -> user, see app.core.invitations).
"""

import threading
import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import invitations, memberships
from app.core.db import SessionLocal, engine
from app.core.tokens import hash_token
from app.models import Role, User
from tests.auth_support import make_session
from tests.factories import add_member, make_org, make_user
from tests.invoicing_support import Request, race, scalar

ROUNDS = 5
PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def session_auth(session_mode):
    pass


@dataclass
class Person:
    user_id: uuid.UUID
    email: str
    headers: dict[str, str]  # a real committed session (Bearer + CSRF)


created_orgs: list[uuid.UUID] = []
created_users: list[uuid.UUID] = []


@pytest.fixture(autouse=True)
def cleanup():
    created_orgs.clear()
    created_users.clear()
    yield
    with engine.begin() as connection:
        connection.execute(text("set local session_replication_role = replica"))
        connection.execute(text("delete from security_events where organization_id = any(:o) or actor_user_id = any(:u)"), {"o": created_orgs, "u": created_users})
        connection.execute(text("delete from auth_sessions where user_id = any(:u)"), {"u": created_users})
        connection.execute(text("delete from user_credentials where user_id = any(:u)"), {"u": created_users})
        connection.execute(text("delete from organization_invitations where organization_id = any(:o)"), {"o": created_orgs})
        connection.execute(text("delete from organization_users where organization_id = any(:o) or user_id = any(:u)"), {"o": created_orgs, "u": created_users})
        connection.execute(text("delete from organizations where id = any(:o)"), {"o": created_orgs})
        connection.execute(text("delete from users where id = any(:u)"), {"u": created_users})


def person(db, **fields) -> Person:
    user = make_user(db, **fields)
    handle = make_session(db, user)
    created_users.append(user.id)
    return Person(user.id, user.email, handle.headers)


class Team:
    """A committed organization whose members have committed sessions."""

    def __init__(self, roles: dict[str, str]):
        with SessionLocal() as db:
            org = make_org(db, f"Invite {uuid.uuid4().hex[:8]}")
            self.org_id = org.id
            created_orgs.append(org.id)
            self.people: dict[str, Person] = {}
            for label, role in roles.items():
                self.people[label] = person(db)
                add_member(db, org, db.get(User, self.people[label].user_id), Role(role))
            db.commit()

    def headers(self, label: str) -> dict[str, str]:
        return {**self.people[label].headers, "X-Organization-Id": str(self.org_id)}

    def invite(self, actor: str, to: str, role: str = "viewer") -> Request:
        return Request(self.headers(actor), "post", "/api/invitations", json={"email": to, "role": role})

    def invite_now(self, actor: str, to: str, role: str = "viewer") -> dict:
        response = self.invite(actor, to, role).result()
        assert response.status_code == 201, response.text
        return response.json()

    def revoke(self, actor: str, invitation_id: str) -> Request:
        return Request(self.headers(actor), "delete", f"/api/invitations/{invitation_id}")

    def members(self) -> int:
        return scalar("select count(*) from organization_users where organization_id = :o", o=self.org_id)

    def role(self, user_id) -> str | None:
        return scalar("select role from organization_users where organization_id = :o and user_id = :u", o=self.org_id, u=user_id)


def invitation_row(token: str):
    with engine.connect() as connection:
        return connection.execute(text("select * from organization_invitations where token_hash = :h"), {"h": hash_token(token)}).one()


def accept(who: Person, token: str) -> Request:
    return Request(who.headers, "post", "/api/invite/accept", json={"token": token})


def accept_new(token: str, name: str = "Racer", password: str = PASSWORD) -> Request:
    return Request({}, "post", "/api/invite/accept-new", json={"token": token, "name": name, "password": password})


def events(kind: str, org_id) -> int:
    return scalar("select count(*) from security_events where organization_id = :o and event_type = :t", o=org_id, t=kind)


def users_with(email: str) -> int:
    return scalar("select count(*) from users where email = :e", e=email)


class Gate:
    """Pause the FIRST call of `module.name` until released (the first request to reach it is the one held)."""

    def __init__(self, monkeypatch, module, name: str):
        self.entered, self.release = threading.Event(), threading.Event()
        self._used, self._lock = False, threading.Lock()
        real = getattr(module, name)

        def gated(*args, **kwargs):
            with self._lock:
                first, self._used = not self._used, True
            if first:
                self.entered.set()
                assert self.release.wait(15)
            return real(*args, **kwargs)

        monkeypatch.setattr(module, name, gated)


def track_new_user(email: str):
    with engine.connect() as connection:
        row = connection.execute(text("select id from users where email = :e"), {"e": email}).first()
    if row:
        created_users.append(row[0])


# --- acceptance races ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("_round", range(ROUNDS))
def test_two_simultaneous_acceptances_by_the_invited_account_create_one_membership(_round):
    team = Team({"o": "owner"})
    with SessionLocal() as db:
        invited = person(db)
        db.commit()
    token = team.invite_now("o", invited.email, "accountant")["token"]

    first, second = race(lambda: accept(invited, token).result(), lambda: accept(invited, token).result())

    assert first.status_code == 200 and second.status_code == 200
    assert sorted([first.json()["joined"], second.json()["joined"]]) == [False, True]  # one created it, the other is the idempotent retry
    assert team.members() == 2 and team.role(invited.user_id) == "accountant"
    assert events("invitation_accepted", team.org_id) == 1
    assert invitation_row(token).accepted_by == invited.user_id


@pytest.mark.parametrize("_round", range(ROUNDS))
def test_an_acceptance_by_the_wrong_account_racing_the_right_one_changes_nothing_for_the_wrong_one(_round):
    team = Team({"o": "owner"})
    with SessionLocal() as db:
        invited, other = person(db), person(db)
        db.commit()
    token = team.invite_now("o", invited.email, "viewer")["token"]

    right, wrong = race(lambda: accept(invited, token).result(), lambda: accept(other, token).result())

    assert right.status_code == 200 and wrong.status_code in (403, 404)
    assert team.role(other.user_id) is None and team.role(invited.user_id) == "viewer"


@pytest.mark.parametrize("_round", range(ROUNDS))
def test_two_simultaneous_account_creations_with_one_token_create_one_account(_round):
    team = Team({"o": "owner"})
    to = f"newcomer-{uuid.uuid4().hex[:8]}@invitees.invalid"
    token = team.invite_now("o", to, "employee")["token"]

    first, second = race(lambda: accept_new(token).result(), lambda: accept_new(token).result())
    track_new_user(to)

    assert sorted([first.status_code, second.status_code]) == [200, 404]
    assert users_with(to) == 1 and team.members() == 2
    assert scalar("select count(*) from user_credentials c join users u on u.id = c.user_id where u.email = :e", e=to) == 1
    assert scalar("select count(*) from auth_sessions s join users u on u.id = s.user_id where u.email = :e", e=to) == 1  # exactly one session was issued
    assert events("invitation_accepted", team.org_id) == 1


@pytest.mark.parametrize("_round", range(ROUNDS))
def test_acceptance_racing_revocation_has_exactly_one_winner(_round):
    team = Team({"o": "owner"})
    with SessionLocal() as db:
        invited = person(db)
        db.commit()
    made = team.invite_now("o", invited.email, "viewer")

    accepted, revoked = race(lambda: accept(invited, made["token"]).result(), lambda: team.revoke("o", made["id"]).result())

    outcome = (accepted.status_code, revoked.status_code)
    assert outcome in ((200, 409), (404, 204)), outcome  # accepted first: revoking an accepted invitation is refused; revoked first: it is unusable
    row = invitation_row(made["token"])
    assert (row.accepted_at is None) != (row.revoked_at is None)  # never both, never neither
    assert (team.role(invited.user_id) == "viewer") == (outcome == (200, 409))


def test_another_path_cannot_slip_a_membership_in_while_an_acceptance_holds_the_account_lock(monkeypatch):
    """Acceptance locks the invited account's user row (the last of the three locks). Inserting a membership for that
    user takes a key-share lock on the same row, so another path that wants to make them a member must WAIT until the
    acceptance has committed; it then meets the unique constraint or a deadlock the database resolves against it. A stale invitation therefore never ends with two
    memberships or a promoted role."""
    team = Team({"o": "owner"})
    with SessionLocal() as db:
        invited = person(db)
        db.commit()
    token = team.invite_now("o", invited.email, "admin")["token"]
    # Pause at the savepoint around the membership INSERT: acceptance has locked everything and is about to insert.
    gate = Gate(monkeypatch, Session, "begin_nested")
    pending = accept(invited, token)
    assert gate.entered.wait(5)

    outcome: list[str] = []

    def other_path():
        try:
            with engine.begin() as connection:
                connection.execute(text("insert into organization_users (organization_id, user_id, role) values (:o, :u, 'viewer')"), {"o": team.org_id, "u": invited.user_id})
            outcome.append("inserted")
        except Exception as error:  # the unique constraint, once the acceptance has committed
            # The database resolves the cycle against this out-of-band writer: it holds a unique-key claim and waits for the
            # account lock, while the acceptance waits for that claim. (No HTTP path inserts memberships except acceptance.)
            outcome.append("refused" if "uq_organization_users_org_user" in str(error) or "deadlock detected" in str(error) else f"error: {error}")

    thread = threading.Thread(target=other_path, daemon=True)
    thread.start()
    thread.join(0.8)
    assert thread.is_alive(), "the other path was not made to wait for the acceptance"
    gate.release.set()

    response = pending.result()
    thread.join(10)
    assert response.status_code == 200 and response.json() == {"organization_id": str(team.org_id), "role": "admin", "joined": True}
    assert outcome == ["refused"]
    assert team.role(invited.user_id) == "admin"
    assert scalar("select count(*) from organization_users where user_id = :u", u=invited.user_id) == 1


@pytest.mark.parametrize("_round", range(ROUNDS))
def test_two_organizations_inviting_the_same_new_email_create_one_account_and_the_loser_can_still_accept(_round):
    first_team, second_team = Team({"o": "owner"}), Team({"o": "owner"})
    to = f"shared-{uuid.uuid4().hex[:8]}@invitees.invalid"
    a = first_team.invite_now("o", to, "viewer")["token"]
    b = second_team.invite_now("o", to, "employee")["token"]

    ra, rb = race(lambda: accept_new(a).result(), lambda: accept_new(b).result())
    track_new_user(to)

    assert sorted([ra.status_code, rb.status_code]) == [200, 409]  # the loser is told the account exists
    assert users_with(to) == 1
    winner, loser_token, loser_team = (ra, b, second_team) if ra.status_code == 200 else (rb, a, first_team)
    assert invitation_row(loser_token).accepted_at is None  # unconsumed
    body = winner.json()
    existing = Person(uuid.UUID(body["user"]["id"]), to, {"Authorization": f"Bearer {body['token']}", "X-CSRF-Token": body["csrf_token"]})
    assert accept(existing, loser_token).result().status_code == 200  # now an ordinary acceptance by the existing account
    assert loser_team.role(existing.user_id) in ("viewer", "employee")


# --- administration races ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("_round", range(ROUNDS))
def test_two_simultaneous_invitations_for_one_email_leave_one_pending(_round):
    team = Team({"o": "owner", "a": "admin"})
    to = f"dup-{uuid.uuid4().hex[:8]}@invitees.invalid"

    first, second = race(lambda: team.invite("o", to, "viewer").result(), lambda: team.invite("a", to, "employee").result())

    assert sorted([first.status_code, second.status_code]) == [201, 409]
    assert scalar("select count(*) from organization_invitations where organization_id = :o and email = :e and revoked_at is null and accepted_at is null", o=team.org_id, e=to) == 1


def test_an_admin_demoted_before_the_lock_loses_the_authority_to_invite_and_to_revoke(monkeypatch):
    team = Team({"o": "owner", "a": "admin"})
    existing = team.invite_now("o", f"x-{uuid.uuid4().hex[:6]}@invitees.invalid", "viewer")
    gate = Gate(monkeypatch, memberships, "_lock_members")
    stale_create = team.invite("a", f"y-{uuid.uuid4().hex[:6]}@invitees.invalid", "viewer")  # authenticated as an admin, then paused
    assert gate.entered.wait(5)

    with engine.begin() as connection:  # the owner demotes the admin meanwhile
        connection.execute(text("update organization_users set role = 'employee' where organization_id = :o and user_id = :u"), {"o": team.org_id, "u": team.people["a"].user_id})
    gate.release.set()

    response = stale_create.result()
    assert response.status_code == 403
    assert scalar("select count(*) from organization_invitations where organization_id = :o", o=team.org_id) == 1
    assert team.revoke("a", existing["id"]).result().status_code == 403  # and a fresh attempt is refused too
    assert invitation_row(existing["token"]).revoked_at is None


def test_an_admin_removed_before_the_lock_cannot_revoke(monkeypatch):
    team = Team({"o": "owner", "a": "admin"})
    existing = team.invite_now("o", f"z-{uuid.uuid4().hex[:6]}@invitees.invalid", "viewer")
    gate = Gate(monkeypatch, memberships, "_lock_members")
    stale_revoke = team.revoke("a", existing["id"])
    assert gate.entered.wait(5)
    with engine.begin() as connection:
        connection.execute(text("delete from organization_users where organization_id = :o and user_id = :u"), {"o": team.org_id, "u": team.people["a"].user_id})
    gate.release.set()
    assert stale_revoke.result().status_code == 404
    assert invitation_row(existing["token"]).revoked_at is None


# --- the new account is atomic ------------------------------------------------------------------------------------------------------


def test_a_failure_while_creating_the_account_leaves_nothing_and_the_invitation_usable(monkeypatch):
    team = Team({"o": "owner"})
    to = f"atomic-{uuid.uuid4().hex[:8]}@invitees.invalid"
    token = team.invite_now("o", to, "viewer")["token"]

    def boom(*args, **kwargs):
        raise RuntimeError("injected after the user, credential and membership were flushed")

    real = invitations.sessions.create_session
    monkeypatch.setattr(invitations.sessions, "create_session", boom)
    assert accept_new(token).result().status_code == 500
    monkeypatch.setattr(invitations.sessions, "create_session", real)  # (not monkeypatch.undo(): that would also undo session mode)

    assert users_with(to) == 0
    assert scalar("select count(*) from user_credentials c join users u on u.id = c.user_id where u.email = :e", e=to) == 0
    assert team.members() == 1  # only the owner
    row = invitation_row(token)
    assert row.accepted_at is None and row.accepted_by is None
    assert events("invitation_accepted", team.org_id) == 0
    assert accept_new(token).result().status_code == 200  # and it can still be used
    track_new_user(to)


# --- the combined lock order: invitations and membership administration together -----------------------------------------------------------


@pytest.mark.parametrize("_round", range(3))
def test_a_burst_of_invitation_and_membership_operations_neither_deadlocks_nor_loses_the_last_owner(_round):
    team = Team({"o1": "owner", "o2": "owner", "a": "admin", "e": "employee", "v": "viewer"})
    with SessionLocal() as db:
        invited = [person(db) for _ in range(3)]
        db.commit()
    made = [team.invite_now("o1", p.email, "viewer") for p in invited]
    extra = team.invite_now("o2", f"extra-{uuid.uuid4().hex[:6]}@invitees.invalid", "employee")
    member_id = lambda label: scalar("select id from organization_users where organization_id = :o and user_id = :u", o=team.org_id, u=team.people[label].user_id)  # noqa: E731

    calls = [
        lambda: accept(invited[0], made[0]["token"]).result(),
        lambda: accept(invited[1], made[1]["token"]).result(),
        lambda: team.revoke("o2", made[2]["id"]).result(),
        lambda: accept(invited[2], made[2]["token"]).result(),
        lambda: team.invite("a", f"n-{uuid.uuid4().hex[:6]}@invitees.invalid", "viewer").result(),
        lambda: Request(team.headers("o1"), "post", f"/api/invitations/{extra['id']}/regenerate").result(),
        lambda: Request(team.headers("o1"), "patch", f"/api/members/{member_id('o2')}", json={"role": "admin"}).result(),
        lambda: Request(team.headers("o2"), "patch", f"/api/members/{member_id('o1')}", json={"role": "admin"}).result(),
        lambda: Request(team.headers("o1"), "delete", f"/api/members/{member_id('v')}").result(),
        lambda: Request(team.headers("a"), "patch", f"/api/members/{member_id('e')}", json={"role": "viewer"}).result(),
        lambda: Request(team.headers("e"), "post", "/api/members/leave").result(),
    ]
    results = race(*calls)

    assert all(r.status_code in (200, 201, 204, 403, 404, 409) for r in results), [r.status_code for r in results]  # never a 500
    assert scalar("select count(*) from organization_users where organization_id = :o and role = 'owner'", o=team.org_id) >= 1
    for p in invited:
        assert scalar("select count(*) from organization_users where organization_id = :o and user_id = :u", o=team.org_id, u=p.user_id) <= 1
