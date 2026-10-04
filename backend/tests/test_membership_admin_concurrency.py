"""Membership administration under real concurrency: committed data, separate connections, genuine waiting.

The lock strategy being proved (app.core.memberships): every mutation first locks ALL membership rows of ONE
organization in ascending id order and decides from those fresh rows. These tests show, with several real database
connections, that: the organization never reaches zero owners, nobody succeeds with stale authority, one organization's
administration never waits for another's, and nothing deadlocks. Some races are gated: a request is paused at a known
point (before its locks, or after them) while the other side acts.
"""

import threading
import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import text

from app.core import memberships
from app.core.db import SessionLocal, engine
from app.models import Role
from tests.factories import add_member, make_org, make_user
from tests.invoicing_support import Request, race, scalar

ROUNDS = 6  # natural races are repeated on fresh data: an interleaving that only sometimes happens must not hide


@pytest.fixture(autouse=True)
def dev_mode(dev_auth):
    pass


@dataclass
class Person:
    user_id: uuid.UUID
    email: str
    membership_id: uuid.UUID


class Committed:
    """A committed organization with named members; `purge` removes everything it created."""

    created: list["Committed"] = []

    def __init__(self, roles: dict[str, str], name: str = "Race"):
        with SessionLocal() as db:
            org = make_org(db, f"{name} {uuid.uuid4().hex[:8]}")
            self.org_id = org.id
            self.people: dict[str, Person] = {}
            for label, role in roles.items():
                user = make_user(db)
                row = add_member(db, org, user, Role(role))
                self.people[label] = Person(user.id, user.email, row.id)
            db.commit()
        Committed.created.append(self)

    def headers(self, label: str, org_id=None) -> dict[str, str]:
        return {"X-Dev-User-Email": self.people[label].email, "X-Organization-Id": str(org_id or self.org_id)}

    def patch(self, actor: str, target: str, role: str) -> Request:
        return Request(self.headers(actor), "patch", f"/api/members/{self.people[target].membership_id}", json={"role": role})

    def delete(self, actor: str, target: str) -> Request:
        return Request(self.headers(actor), "delete", f"/api/members/{self.people[target].membership_id}")

    def leave(self, actor: str) -> Request:
        return Request(self.headers(actor), "post", "/api/members/leave")

    def role(self, label: str) -> str | None:
        return scalar("select role from organization_users where id = :i", i=self.people[label].membership_id)

    def owners(self) -> int:
        return scalar("select count(*) from organization_users where organization_id = :o and role = 'owner'", o=self.org_id)

    def members(self) -> int:
        return scalar("select count(*) from organization_users where organization_id = :o", o=self.org_id)

    def events(self, event_type: str) -> int:
        return scalar("select count(*) from security_events where organization_id = :o and event_type = :t", o=self.org_id, t=event_type)

    def purge(self):
        ids = [p.user_id for p in self.people.values()]
        with engine.begin() as connection:
            connection.execute(text("set local session_replication_role = replica"))
            connection.execute(text("delete from security_events where organization_id = :o or actor_user_id = any(:u)"), {"o": self.org_id, "u": ids})
            connection.execute(text("delete from organization_users where organization_id = :o"), {"o": self.org_id})
            connection.execute(text("delete from organizations where id = :o"), {"o": self.org_id})
            connection.execute(text("delete from users where id = any(:u)"), {"u": ids})


@pytest.fixture(autouse=True)
def cleanup():
    Committed.created = []
    yield
    for team in Committed.created:
        team.purge()


class Gate:
    """Pause the FIRST call of a function of app.core.memberships (before or after the locks), until released."""

    def __init__(self, monkeypatch, name: str):
        self.entered, self.release = threading.Event(), threading.Event()
        self._used = False
        self._lock = threading.Lock()
        real = getattr(memberships, name)

        def gated(*args, **kwargs):
            with self._lock:
                first, self._used = not self._used, True
            if first:
                self.entered.set()
                assert self.release.wait(15)
            return real(*args, **kwargs)

        monkeypatch.setattr(memberships, name, gated)


OWNERS = {"o1": "owner", "o2": "owner"}


# --- natural races: the organization never reaches zero owners ---------------------------------------------------------------------------


@pytest.mark.parametrize("_round", range(ROUNDS))
def test_two_owners_demoting_themselves_at_once_leave_exactly_one(_round):
    team = Committed(OWNERS)
    a, b = race(lambda: team.patch("o1", "o1", "admin").result(), lambda: team.patch("o2", "o2", "admin").result())
    assert sorted([a.status_code, b.status_code]) == [200, 409]
    assert team.owners() == 1 and team.events("member_role_changed") == 1


@pytest.mark.parametrize("_round", range(ROUNDS))
def test_two_owners_leaving_at_once_leave_exactly_one(_round):
    team = Committed(OWNERS)
    a, b = race(lambda: team.leave("o1").result(), lambda: team.leave("o2").result())
    assert sorted([a.status_code, b.status_code]) == [204, 409]
    assert team.owners() == 1 and team.members() == 1 and team.events("member_left") == 1


@pytest.mark.parametrize("_round", range(ROUNDS))
def test_two_owners_removing_each_other_leave_exactly_one(_round):
    team = Committed(OWNERS)
    a, b = race(lambda: team.delete("o1", "o2").result(), lambda: team.delete("o2", "o1").result())
    assert sorted([a.status_code, b.status_code]) == [204, 404]  # the loser's own membership is gone: the organization no longer exists for them
    assert team.owners() == 1 and team.members() == 1


@pytest.mark.parametrize("_round", range(ROUNDS))
def test_owner_a_removes_owner_b_while_b_demotes_a(_round):
    team = Committed(OWNERS)
    removal, demotion = race(lambda: team.delete("o1", "o2").result(), lambda: team.patch("o2", "o1", "admin").result())
    outcomes = (removal.status_code, demotion.status_code)
    assert outcomes in ((204, 404), (403, 200)), outcomes  # removal first: B is gone; demotion first: A is no longer an owner
    assert team.owners() == 1


@pytest.mark.parametrize("_round", range(ROUNDS))
def test_promotion_racing_a_sole_owner_leaving_never_leaves_none(_round):
    team = Committed({"o1": "owner", "e": "employee"})
    promote, leaving = race(lambda: team.patch("o1", "e", "owner").result(), lambda: team.leave("o1").result())
    # Leaving first is refused (sole owner, 409) and the promotion then succeeds; promoting first lets the owner go.
    assert (promote.status_code, leaving.status_code) in ((200, 204), (200, 409)), (promote.status_code, leaving.status_code)
    assert team.role("e") == "owner"
    assert team.owners() == (1 if leaving.status_code == 204 else 2)


@pytest.mark.parametrize("_round", range(ROUNDS))
def test_demoting_the_other_owner_races_promoting_a_third(_round):
    team = Committed({"o1": "owner", "o2": "owner", "e": "employee"})
    results = race(
        lambda: team.patch("o1", "o2", "viewer").result(),
        lambda: team.patch("o2", "o1", "viewer").result(),
        lambda: team.patch("o1", "e", "owner").result(),
    )
    assert all(r.status_code in (200, 403, 404, 409) for r in results)
    assert team.owners() >= 1


# --- natural races: no stale authority --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("_round", range(ROUNDS))
def test_a_member_leaving_races_an_administrator_removing_them(_round):
    team = Committed({"o": "owner", "e": "employee"})
    left, removed = race(lambda: team.leave("e").result(), lambda: team.delete("o", "e").result())
    assert sorted([left.status_code, removed.status_code]) == [204, 404]
    assert team.role("e") is None
    assert team.events("member_left") + team.events("member_removed") == 1  # exactly one of the two happened


@pytest.mark.parametrize("_round", range(ROUNDS))
def test_a_role_change_races_the_removal_of_the_same_membership(_round):
    team = Committed({"o": "owner", "a": "admin", "v": "viewer"})
    changed, removed = race(lambda: team.patch("o", "v", "employee").result(), lambda: team.delete("a", "v").result())
    assert (changed.status_code, removed.status_code) in ((200, 204), (404, 204))
    assert team.role("v") is None


# --- gated races: a request paused BEFORE its locks acts on what it finds after them ------------------------------------------------------


def test_an_admin_demoted_after_authentication_but_before_the_lock_loses_the_authority(monkeypatch):
    team = Committed({"o": "owner", "a": "admin", "v": "viewer"})
    gate = Gate(monkeypatch, "_lock_members")
    admin_action = team.delete("a", "v")  # authenticated as an admin (the tenant context said so), then paused
    assert gate.entered.wait(5)

    assert team.patch("o", "a", "employee").result().status_code == 200  # the owner demotes the admin meanwhile
    gate.release.set()

    response = admin_action.result()
    assert response.status_code == 403 and response.json()["detail"]["code"] == "membership_admin_forbidden"
    assert team.role("v") == "viewer"


def test_a_target_promoted_to_admin_before_the_lock_is_out_of_an_admins_reach(monkeypatch):
    team = Committed({"o": "owner", "a": "admin", "e": "employee"})
    gate = Gate(monkeypatch, "_lock_members")
    admin_action = team.delete("a", "e")
    assert gate.entered.wait(5)

    assert team.patch("o", "e", "admin").result().status_code == 200
    gate.release.set()

    response = admin_action.result()
    assert response.status_code == 403 and response.json()["detail"]["code"] == "insufficient_authority"
    assert team.role("e") == "admin"


def test_an_admin_removed_before_the_lock_is_no_longer_in_the_organization(monkeypatch):
    team = Committed({"o": "owner", "a": "admin", "v": "viewer"})
    gate = Gate(monkeypatch, "_lock_members")
    admin_action = team.patch("a", "v", "employee")
    assert gate.entered.wait(5)

    assert team.delete("o", "a").result().status_code == 204
    gate.release.set()

    assert admin_action.result().status_code == 404  # the organization no longer exists for them
    assert team.role("v") == "viewer"


def test_a_target_removed_before_the_lock_is_not_found(monkeypatch):
    team = Committed({"o": "owner", "a": "admin", "v": "viewer"})
    gate = Gate(monkeypatch, "_lock_members")
    role_change = team.patch("o", "v", "employee")
    assert gate.entered.wait(5)

    assert team.delete("a", "v").result().status_code == 204
    gate.release.set()

    response = role_change.result()
    assert response.status_code == 404 and response.json() == {"detail": "Member not found"}


def test_an_owner_demoted_before_the_lock_cannot_use_the_old_owner_authority(monkeypatch):
    team = Committed({"o1": "owner", "o2": "owner", "e": "employee"})
    gate = Gate(monkeypatch, "_lock_members")
    stale_owner_action = team.patch("o2", "e", "admin")  # an owner may grant admin; an admin may not
    assert gate.entered.wait(5)

    assert team.patch("o1", "o2", "admin").result().status_code == 200
    gate.release.set()

    assert stale_owner_action.result().status_code == 403
    assert team.role("e") == "employee"


# --- gated races: a request paused AFTER its locks holds them -----------------------------------------------------------------------------


def test_a_mutation_holding_the_locks_blocks_another_in_the_same_organization_but_not_one_in_another(monkeypatch):
    mine, other = Committed({"o": "owner", "e": "employee"}), Committed({"o": "owner", "e": "employee"})
    gate = Gate(monkeypatch, "_event")  # runs after the locks are taken and the change flushed
    holder = mine.patch("o", "e", "viewer")
    assert gate.entered.wait(5)

    waiting = mine.patch("o", "e", "accountant")  # same organization: must wait for the locks
    assert waiting.still_waiting()
    assert other.patch("o", "e", "viewer").result().status_code == 200  # another organization: not blocked at all

    gate.release.set()
    assert holder.result().status_code == 200 and waiting.result().status_code == 200
    assert mine.role("e") == "accountant"  # the second ran after the first, on fresh rows


def test_a_blocked_decision_is_made_from_the_state_the_first_mutation_left(monkeypatch):
    team = Committed({"o": "owner", "a": "admin", "v": "viewer"})
    gate = Gate(monkeypatch, "_event")
    first = team.patch("o", "a", "viewer")  # the owner demotes the admin, and holds the locks
    assert gate.entered.wait(5)
    second = team.delete("a", "v")  # the admin acts concurrently, authenticated as an admin
    assert second.still_waiting()

    gate.release.set()
    assert first.result().status_code == 200
    assert second.result().status_code == 403  # decided after the demotion
    assert team.role("v") == "viewer"


# --- many operations at once: no deadlock, no 500, the invariant holds --------------------------------------------------------------------------


@pytest.mark.parametrize("_round", range(3))
def test_a_burst_of_mixed_administration_neither_deadlocks_nor_loses_the_last_owner(_round):
    team = Committed({"o1": "owner", "o2": "owner", "a1": "admin", "a2": "admin", "c": "accountant", "e": "employee", "v": "viewer", "w": "viewer"})
    calls = [
        lambda: team.patch("o1", "o2", "admin").result(),
        lambda: team.patch("o2", "o1", "admin").result(),
        lambda: team.delete("o1", "o2").result(),
        lambda: team.delete("o2", "o1").result(),
        lambda: team.leave("o1").result(),
        lambda: team.leave("o2").result(),
        lambda: team.patch("a1", "e", "viewer").result(),
        lambda: team.patch("a2", "a1", "employee").result(),
        lambda: team.patch("a1", "a2", "employee").result(),
        lambda: team.delete("a1", "v").result(),
        lambda: team.patch("o1", "w", "owner").result(),
        lambda: team.leave("c").result(),
        lambda: team.patch("e", "v", "admin").result(),
        lambda: team.patch("o2", "c", "owner").result(),
    ]
    results = race(*calls)
    assert all(r.status_code in (200, 204, 403, 404, 409) for r in results), [r.status_code for r in results]  # never a 500
    assert team.owners() >= 1


def test_an_organization_never_touched_by_a_race_is_untouched():
    bystander = Committed({"o": "owner", "e": "employee", "v": "viewer"})
    team = Committed(OWNERS)
    race(lambda: team.delete("o1", "o2").result(), lambda: team.delete("o2", "o1").result())
    assert (bystander.owners(), bystander.members()) == (1, 3)
    assert bystander.events("member_removed") == bystander.events("member_role_changed") == 0


# --- the database backstop under concurrency (raw SQL, bypassing the application) -----------------------------------------------------------


def test_two_raw_transactions_each_removing_a_different_owner_cannot_both_commit():
    """The classic write skew: without the advisory lock each transaction would see the OTHER owner and both would commit."""
    team = Committed(OWNERS)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def demote(label: str):
        connection = engine.connect()
        try:
            connection.execute(text("update organization_users set role = 'admin' where id = :i"), {"i": team.people[label].membership_id})
            barrier.wait(10)  # both have changed their own row; neither has committed
            connection.commit()
            outcomes.append("committed")
        except Exception as error:  # the deferred trigger refuses at commit
            connection.rollback()
            outcomes.append("refused" if "without an owner" in str(error) else f"error: {error}")
        finally:
            connection.close()

    threads = [threading.Thread(target=demote, args=(label,), daemon=True) for label in ("o1", "o2")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
        assert not thread.is_alive(), "a raw transaction hung (deadlock?)"

    assert sorted(outcomes) == ["committed", "refused"], outcomes
    assert team.owners() == 1


# --- the legacy ownerless organization at a REAL commit ------------------------------------------------------------------------------------


def test_an_ownerless_organization_commits_ordinary_administration_and_the_operator_can_repair_it():
    team = Committed({"a": "admin", "e": "employee", "v": "viewer"})
    assert team.owners() == 0
    assert team.patch("a", "e", "viewer").result().status_code == 200
    assert team.delete("a", "v").result().status_code == 204
    assert team.leave("e").result().status_code == 204  # real commits; the backstop does not object to an organization that never had an owner

    from app.scripts import repair

    with SessionLocal() as db:
        email = db.execute(text("select email from users where id = :u"), {"u": team.people["a"].user_id}).scalar_one()
        repair.repair_owner(db, organization_id=team.org_id, email=email)
    assert team.owners() == 1 and team.role("a") == "owner"
