"""Organization creation under real concurrency: committed data, separate connections, genuine waiting.

What these prove that the rollback-only tests cannot: the organization and its owner membership are committed
together or not at all (seen from ANOTHER connection), simultaneous creations and retries stay consistent, and a
capability change that races with a creation is serialized by the user-row lock (fresh permission, no deadlock).
"""

import secrets
import threading
import types

import pytest

from app.core import auth_service
from app.core import organizations as service
from app.scripts import admin
from tests.auth_support import CommittedUser, build_committed_user, purge_committed
from tests.invoicing_support import Request, race, scalar

URL = "/api/organizations"
REAL_BETWEEN_INSERTS = service._between_inserts


@pytest.fixture(autouse=True)
def committed(dev_auth):
    created: list[CommittedUser] = []
    yield created
    purge_committed(created)


@pytest.fixture
def make(committed):
    def build(**fields) -> CommittedUser:
        user = build_committed_user(with_credential=False, can_create_organizations=fields.pop("can_create_organizations", True), **fields)
        committed.append(user)
        return user

    return build


def key() -> str:
    return secrets.token_urlsafe(32)


def post(user: CommittedUser, name: str, request_key: str | None = None) -> Request:
    headers = {"X-Dev-User-Email": user.email}
    if request_key:
        headers["Idempotency-Key"] = request_key
    return Request(headers, "post", URL, json={"name": name, "default_currency": "EUR"})


def organizations_of(user: CommittedUser) -> list[tuple[str, str]]:
    """(organization name, role) for every membership of the user, read on a fresh connection."""
    from sqlalchemy import text

    from app.core.db import engine

    with engine.connect() as connection:
        return [tuple(r) for r in connection.execute(text("select o.name, ou.role from organization_users ou join organizations o on o.id = ou.organization_id where ou.user_id = :u order by o.name"), {"u": user.id})]


def organization_rows(name: str) -> int:
    return scalar("select count(*) from organizations where name = :n", n=name)


def counts(user: CommittedUser) -> dict[str, int]:
    return {
        "requests": scalar("select count(*) from organization_creation_requests where user_id = :u", u=user.id),
        "memberships": scalar("select count(*) from organization_users where user_id = :u", u=user.id),
        "events": scalar("select count(*) from security_events where actor_user_id = :u and event_type = 'organization_created'", u=user.id),
    }


# --- atomicity, seen from another connection --------------------------------------------------------------------------------


def test_a_failure_between_the_two_inserts_commits_nothing(make, monkeypatch):
    user, name = make(), f"Atomic {secrets.token_hex(4)}"
    gate, entered = threading.Event(), threading.Event()

    def pause_then_fail():
        entered.set()
        gate.wait(10)
        raise RuntimeError("injected between the organization and its owner membership")

    monkeypatch.setattr(service, "_between_inserts", pause_then_fail)
    request = post(user, name, key())
    assert entered.wait(5)

    # While the creation is open, NOTHING of it is visible to another connection (the organization is not committed alone).
    assert organization_rows(name) == 0 and counts(user) == {"requests": 0, "memberships": 0, "events": 0}
    gate.set()

    assert request.result().status_code == 500
    assert organization_rows(name) == 0
    assert counts(user) == {"requests": 0, "memberships": 0, "events": 0}


def test_a_failure_recording_the_event_commits_nothing(make, monkeypatch):
    user, name = make(), f"NoEvent {secrets.token_hex(4)}"
    monkeypatch.setattr(service, "SecurityEvent", lambda **k: (_ for _ in ()).throw(RuntimeError("injected")))

    assert post(user, name, key()).result().status_code == 500
    assert organization_rows(name) == 0 and counts(user) == {"requests": 0, "memberships": 0, "events": 0}


def test_a_success_commits_everything_together_and_a_failed_attempt_can_be_retried_with_the_same_key(make, monkeypatch):
    user, name, request_key = make(), f"Retry {secrets.token_hex(4)}", key()
    monkeypatch.setattr(service, "_between_inserts", lambda: (_ for _ in ()).throw(RuntimeError("injected")))
    assert post(user, name, request_key).result().status_code == 500
    monkeypatch.setattr(service, "_between_inserts", REAL_BETWEEN_INSERTS)

    retried = post(user, name, request_key).result()  # the failed attempt left no request record to collide with
    assert retried.status_code == 201
    assert organizations_of(user) == [(name, "owner")] and counts(user) == {"requests": 1, "memberships": 1, "events": 1}


# --- retries and simultaneous creations ----------------------------------------------------------------------------------------


def test_a_committed_creation_whose_response_was_lost_is_returned_to_the_retry(make):
    user, name, request_key = make(), f"Lost {secrets.token_hex(4)}", key()
    first = post(user, name, request_key).result()  # imagine the browser never saw this answer
    retry = post(user, name, request_key).result()

    assert (first.status_code, retry.status_code) == (201, 200)
    assert retry.json()["id"] == first.json()["id"]
    assert organization_rows(name) == 1 and counts(user) == {"requests": 1, "memberships": 1, "events": 1}


def test_two_simultaneous_creations_with_the_same_key_create_one_organization(make):
    user, name, request_key = make(), f"SameKey {secrets.token_hex(4)}", key()

    responses = race(*[lambda: post(user, name, request_key).result() for _ in range(6)])

    assert sorted(r.status_code for r in responses) == [200] * 5 + [201]
    assert len({r.json()["id"] for r in responses}) == 1
    assert organization_rows(name) == 1 and counts(user) == {"requests": 1, "memberships": 1, "events": 1}


def test_two_simultaneous_creations_with_different_keys_create_two_organizations_each_with_one_owner(make):
    user, name = make(), f"Twin {secrets.token_hex(4)}"

    responses = race(lambda: post(user, name, key()).result(), lambda: post(user, name, key()).result())

    assert [r.status_code for r in responses] == [201, 201]
    assert responses[0].json()["id"] != responses[1].json()["id"]
    assert organizations_of(user) == [(name, "owner"), (name, "owner")]
    assert counts(user) == {"requests": 2, "memberships": 2, "events": 2}
    assert scalar("select count(*) from organization_users where organization_id = any(:o) and role = 'owner'", o=[r.json()["id"] for r in responses]) == 2


def test_different_users_using_the_same_key_each_get_their_own_organization(make):
    first, second, request_key = make(), make(), key()
    name = f"Shared {secrets.token_hex(4)}"

    responses = race(lambda: post(first, name, request_key).result(), lambda: post(second, name, request_key).result())

    assert [r.status_code for r in responses] == [201, 201]
    assert organizations_of(first) == [(name, "owner")] and organizations_of(second) == [(name, "owner")]
    assert organization_rows(name) == 2


# --- the capability changes while a creation is in flight -----------------------------------------------------------------------


def test_a_revoke_that_commits_before_the_decision_refuses_a_request_that_was_already_authenticated(make, monkeypatch):
    """The request has loaded its user (flag true) and then stops just before the permission is judged. The operator
    revokes in between. The decision must use the database's current value, not the loaded object."""
    user, name = make(), f"Revoked {secrets.token_hex(4)}"
    gate, entered = threading.Event(), threading.Event()
    real_lock = auth_service.lock_user

    def paused_lock(db, user_id):
        entered.set()
        gate.wait(10)
        return real_lock(db, user_id)

    monkeypatch.setattr(service, "auth_service", types.SimpleNamespace(lock_user=paused_lock))
    request = post(user, name, key())
    assert entered.wait(5)

    from app.core.db import SessionLocal

    with SessionLocal() as db:
        admin.set_org_creation(db, email=user.email, allowed=False)
    gate.set()

    assert request.result().status_code == 403
    assert organization_rows(name) == 0 and counts(user) == {"requests": 0, "memberships": 0, "events": 0}


def test_a_user_disabled_while_the_request_is_in_flight_creates_nothing(make, monkeypatch):
    user, name = make(), f"Disabled {secrets.token_hex(4)}"
    gate, entered = threading.Event(), threading.Event()
    real_lock = auth_service.lock_user

    def paused_lock(db, user_id):
        entered.set()
        gate.wait(10)
        return real_lock(db, user_id)

    monkeypatch.setattr(service, "auth_service", types.SimpleNamespace(lock_user=paused_lock))
    request = post(user, name, key())
    assert entered.wait(5)

    from app.core.db import SessionLocal

    with SessionLocal() as db:
        admin.disable_user(db, email=user.email)  # authenticated a moment ago; disabled before the decision
    gate.set()

    assert request.result().status_code == 401
    assert organization_rows(name) == 0 and counts(user) == {"requests": 0, "memberships": 0, "events": 0}


def test_a_revoke_waits_for_a_creation_in_flight_which_completes_under_the_right_it_held(make, monkeypatch):
    user, name = make(), f"InFlight {secrets.token_hex(4)}"
    gate, entered = threading.Event(), threading.Event()

    def pause():
        entered.set()
        gate.wait(10)

    monkeypatch.setattr(service, "_between_inserts", pause)
    creation = post(user, name, key())
    assert entered.wait(5)  # the creation holds the user-row lock and has inserted the organization

    revoke_done = threading.Event()

    def revoke():
        from app.core.db import SessionLocal

        with SessionLocal() as db:
            admin.set_org_creation(db, email=user.email, allowed=False)
        revoke_done.set()

    threading.Thread(target=revoke, daemon=True).start()
    assert not revoke_done.wait(0.8), "the revoke did not wait for the creation in flight"

    gate.set()
    assert creation.result().status_code == 201
    assert revoke_done.wait(10)
    assert organizations_of(user) == [(name, "owner")]  # the organization it created stays; the right is gone for the next one
    monkeypatch.setattr(service, "_between_inserts", REAL_BETWEEN_INSERTS)
    assert post(user, f"After {name}", key()).result().status_code == 403


def test_mixed_creations_and_capability_changes_neither_deadlock_nor_leave_a_bare_organization(make):
    from app.core.db import SessionLocal

    user, prefix = make(), f"Mixed {secrets.token_hex(4)}"

    def toggle(allowed: bool):
        def run():
            with SessionLocal() as db:
                admin.set_org_creation(db, email=user.email, allowed=allowed)
            return None

        return run

    calls = []
    for i in range(8):
        calls.append(lambda i=i: post(user, f"{prefix} {i}", key()).result())
        calls.append(toggle(i % 2 == 0))
    results = race(*calls)

    statuses = [r.status_code for r in results if r is not None]
    assert set(statuses) <= {201, 403} and len(statuses) == 8  # nothing hung, nothing crashed
    created = statuses.count(201)
    assert scalar("select count(*) from organizations where name like :p", p=prefix + "%") == created
    assert scalar("select count(*) from organization_users where user_id = :u and role = 'owner'", u=user.id) == created
    # No organization without exactly one owner membership, whichever interleaving happened.
    assert scalar(
        "select count(*) from organizations o where o.name like :p and (select count(*) from organization_users ou where ou.organization_id = o.id and ou.role = 'owner') <> 1",
        p=prefix + "%",
    ) == 0
