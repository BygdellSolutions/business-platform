"""Authentication under real concurrency: committed data, separate connections, genuine waiting.

Natural races (requests started at the same instant) must always end in a consistent state; gated races pause a
request at a known point so the other side's behaviour can be observed.
"""

import threading
import time

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import passwords
from app.core.config import settings
from app.core.db import SessionLocal, engine
from app.scripts import admin
from tests.auth_support import OTHER_PASSWORD, PASSWORD, CommittedUser, build_committed_user, purge_committed
from tests.invoicing_support import Request, race, scalar

pytestmark = pytest.mark.usefixtures("session_mode")


@pytest.fixture(autouse=True)
def committed(session_mode, monkeypatch):
    monkeypatch.setattr(settings, "trust_client_ip_header", True)
    created: list[CommittedUser] = []
    extra_sources: list[str] = []
    yield created, extra_sources
    purge_committed(created, tuple(extra_sources))


@pytest.fixture
def make(committed):
    created, extra_sources = committed

    def build(**kwargs) -> CommittedUser:
        user = build_committed_user(**kwargs)
        created.append(user)
        return user

    return build


def attempt(user: CommittedUser, password: str = PASSWORD, *, email: str | None = None, source: str | None = None) -> Request:
    return Request({"X-Client-Ip": source or user.source}, "post", "/api/auth/login", json={"email": email or user.email, "password": password})


def attempt_now(user: CommittedUser, password: str = PASSWORD, **kwargs):
    return attempt(user, password, **kwargs).result()


def usable_sessions(user: CommittedUser) -> int:
    return scalar(
        "select count(*) from auth_sessions where user_id = :u and revoked_at is null and absolute_expires_at > now()",
        u=user.id,
    )


def widen_admission(monkeypatch, concurrent=8, waiting=200):
    monkeypatch.setattr(settings, "argon2_max_concurrent", concurrent)
    monkeypatch.setattr(settings, "argon2_max_waiting", waiting)
    monkeypatch.setattr(settings, "argon2_wait_seconds", 20.0)
    passwords.configure_admission_from_settings()


# --- session cap -----------------------------------------------------------------------------------------------------------------------------------


def test_simultaneous_logins_never_leave_more_active_sessions_than_the_cap(make, monkeypatch):
    monkeypatch.setattr(settings, "session_max_per_user", 5)
    widen_admission(monkeypatch)
    user = make()

    responses = race(*[lambda: attempt_now(user) for _ in range(16)])

    assert {r.status_code for r in responses} == {200}
    assert usable_sessions(user) == 5
    assert scalar("select count(*) from auth_sessions where user_id = :u", u=user.id) == 16
    assert scalar("select count(*) from auth_sessions where user_id = :u and revoked_reason = 'cap'", u=user.id) == 11


# --- setup links -----------------------------------------------------------------------------------------------------------------------------------


def test_two_simultaneous_redemptions_of_one_link_exactly_one_wins(make, monkeypatch):
    widen_admission(monkeypatch)
    for _ in range(8):
        user = make(with_credential=False)
        with SessionLocal() as db:
            from app.core import auth_service, clock
            from app.models import User
            from sqlalchemy import select

            token = auth_service.issue_setup_token(db, db.scalar(select(User).where(User.id == user.id)), clock.utcnow())
            db.commit()
        first, second = race(
            lambda: Request({"X-Client-Ip": user.source}, "post", "/api/auth/setup", json={"token": token, "password": "first long passphrase here"}).result(),
            lambda: Request({"X-Client-Ip": user.source}, "post", "/api/auth/setup", json={"token": token, "password": "second long passphrase here"}).result(),
        )
        assert sorted([first.status_code, second.status_code]) == [200, 400]
        winner = "first long passphrase here" if first.status_code == 200 else "second long passphrase here"
        loser = "second long passphrase here" if first.status_code == 200 else "first long passphrase here"
        assert attempt_now(user, winner).status_code == 200
        assert attempt_now(user, loser).status_code == 401  # only the winner's password was ever set
        assert scalar("select count(*) from user_setup_tokens where user_id = :u and used_at is not null", u=user.id) == 1


# --- disabling vs login ------------------------------------------------------------------------------------------------------------------------------


def test_a_login_racing_a_disable_never_leaves_a_usable_session(make, monkeypatch):
    widen_admission(monkeypatch)
    user = make()
    for _ in range(10):
        def disable():
            with SessionLocal() as db:
                admin.disable_user(db, email=user.email)

        results = race(lambda: attempt_now(user), disable)
        assert results[0].status_code in (200, 401, 429)  # 429: earlier rounds' failed logins reached the pair limit
        assert usable_sessions(user) == 0, "a session survived the disabling"
        with SessionLocal() as db:
            admin.enable_user(db, email=user.email)


# --- bounded hashing at the API ----------------------------------------------------------------------------------------------------------------------


def test_requests_beyond_the_hashing_capacity_are_refused_at_once_and_do_no_work(make, monkeypatch):
    monkeypatch.setattr(settings, "argon2_max_concurrent", 1)
    monkeypatch.setattr(settings, "argon2_max_waiting", 1)
    monkeypatch.setattr(settings, "argon2_wait_seconds", 10.0)
    passwords.configure_admission_from_settings()
    user = make()
    gate, entered = threading.Event(), threading.Event()
    running, peak, calls = [0], [0], [0]
    lock = threading.Lock()
    real = passwords._argon_verify

    def slow_verify(stored, password):
        with lock:
            calls[0] += 1
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        entered.set()
        gate.wait(10)
        try:
            return real(stored, password)
        finally:
            with lock:
                running[0] -= 1

    monkeypatch.setattr(passwords, "_argon_verify", slow_verify)

    running_request = attempt(user)  # takes the only slot and blocks inside verification
    assert entered.wait(5)
    waiting_request = attempt(user)  # waits for the slot
    time.sleep(0.4)
    began = time.monotonic()
    refused = [attempt(user) for _ in range(4)]
    results = [r.result() for r in refused]
    assert time.monotonic() - began < 3  # refused immediately, not queued behind the 10 s wait

    assert {r.status_code for r in results} == {503}
    assert all(r.headers["retry-after"] == "1" and r.json()["detail"]["code"] == "auth_busy" for r in results)
    assert calls[0] == 1  # the refused requests did no hashing, and the waiter has not started yet
    assert scalar("select count(*) from security_events where source = :s", s=user.source) == 0  # and wrote nothing

    gate.set()
    assert running_request.result().status_code == 200 and waiting_request.result().status_code == 200
    assert peak[0] == 1 and calls[0] == 2  # never two hashes at once


# --- bounded throttling under a burst ---------------------------------------------------------------------------------------------------------------


def test_a_burst_from_one_source_overshoots_its_failure_budget_by_at_most_the_requests_in_flight(make, monkeypatch):
    monkeypatch.setattr(settings, "throttle_source_max_failures", 8)
    admission_limit = settings.argon2_max_concurrent + settings.argon2_max_waiting
    user = make()

    responses = race(*[lambda i=i: attempt_now(user, OTHER_PASSWORD, email=f"burst-{i}@tests.invalid") for i in range(40)])

    codes = [r.status_code for r in responses]
    assert set(codes) <= {401, 429, 503}
    rows = scalar("select count(*) from security_events where event_type = 'login_failure' and source = :s", s=user.source)
    assert 8 <= rows <= 8 + admission_limit  # the budget, plus at most what was already past the check
    assert attempt_now(user, OTHER_PASSWORD, email="after@tests.invalid").status_code == 429  # and then it is closed
    assert scalar("select count(*) from security_events where event_type = 'login_failure' and source = :s", s=user.source) == rows


def test_a_burst_against_one_account_cannot_lock_the_account_for_its_owner(make, monkeypatch):
    victim = make()
    ok = attempt_now(victim)  # the victim has logged in from their usual address
    assert ok.status_code == 200
    attackers = [f"198.51.100.{i}" for i in range(1, 9)]
    race(*[lambda a=a: [attempt_now(victim, OTHER_PASSWORD, source=a) for _ in range(10)] for a in attackers])
    # Whatever the attackers did, the victim's own address still logs in.
    assert attempt_now(victim).status_code == 200
    created_sources = attackers
    purge_committed([], tuple(created_sources))


# --- the operator CLI end to end -------------------------------------------------------------------------------------------------------------------------


def test_the_cli_bootstraps_a_user_who_can_set_a_password_and_sign_in(capsys, committed, monkeypatch):
    created, _ = committed
    email = "cli-owner@tests.invalid"
    try:
        assert admin.main(["bootstrap-user", "--email", email, "--name", "CLI Owner"]) == 0
        output = capsys.readouterr().out
        link = next(line.strip() for line in output.splitlines() if "/setup#" in line)
        token = link.split("#", 1)[1]
        assert admin.main(["bootstrap-user", "--email", email, "--name", "Again"]) == 1  # no duplicates; the error goes to stderr
        assert "already exists" in capsys.readouterr().err

        response = Request({"X-Client-Ip": "203.0.113.200"}, "post", "/api/auth/setup", json={"token": token, "password": "a long passphrase from the cli"}).result()
        assert response.status_code == 200 and response.json()["user"]["email"] == email
        assert Request({"X-Client-Ip": "203.0.113.200"}, "post", "/api/auth/login", json={"email": email, "password": "a long passphrase from the cli"}).result().status_code == 200
    finally:
        with engine.begin() as connection:
            connection.execute(text("set local session_replication_role = replica"))
            ids = connection.execute(text("select id from users where email = :e"), {"e": email}).scalars().all()
            for table, column in (("security_events", "actor_user_id"), ("auth_sessions", "user_id"), ("user_setup_tokens", "user_id"), ("user_credentials", "user_id")):
                connection.execute(text(f"delete from {table} where {column} = any(:i)"), {"i": ids})
            connection.execute(text("delete from security_events where source = '203.0.113.200'"))
            connection.execute(text("delete from users where id = any(:i)"), {"i": ids})
    assert created == []
