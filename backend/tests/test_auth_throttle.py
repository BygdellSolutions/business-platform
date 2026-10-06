"""Login abuse protection: bounded, source-aware, and never an account lock an attacker can hold.

The algorithm is documented in app/core/throttle.py and docs/architecture.md ("Login abuse protection").
"""

from datetime import timedelta

import pytest
from sqlalchemy import text
from starlette.requests import Request

from app.core import clock, internal_auth, passwords, security_events
from pydantic import SecretStr

from app.core.config import settings
from tests.auth_support import BFF_SECRET, OTHER_PASSWORD, PASSWORD, events, login, login_user

pytestmark = pytest.mark.usefixtures("session_mode")


@pytest.fixture(autouse=True)
def client_ip_from_header(session_mode, monkeypatch):
    monkeypatch.setattr(settings, "trust_client_ip_header", True)
    monkeypatch.setattr(settings, "bff_internal_secret", SecretStr(BFF_SECRET))  # the client address is believed only from the BFF


@pytest.fixture
def verifies(monkeypatch):
    calls: list[str] = []
    real = passwords._argon_verify
    monkeypatch.setattr(passwords, "_argon_verify", lambda stored, password: calls.append(stored) or real(stored, password))
    return calls


def src(address: str) -> dict[str, str]:
    return {"X-Client-Ip": address}


def fail(client, email, address, password=OTHER_PASSWORD):
    return login(client, email, password, headers=src(address))


def failures(db) -> int:
    return db.scalar(text("select count(*) from security_events where event_type = 'login_failure'"))


def set_limits(monkeypatch, **limits):
    for name, value in limits.items():
        monkeypatch.setattr(settings, name, value)


# --- per (source, identifier) --------------------------------------------------------------------------------------------------------------------


def test_the_pair_limit_blocks_that_source_for_that_identifier_before_any_hashing(session_client, db_session, verifies):
    user = login_user(db_session)
    for _ in range(settings.throttle_pair_max_failures):
        assert fail(session_client, user.email, "198.51.100.1").status_code == 401
    verifies.clear()
    before = failures(db_session)

    blocked = fail(session_client, user.email, "198.51.100.1")
    even_right = login(session_client, user.email, PASSWORD, headers=src("198.51.100.1"))

    assert blocked.status_code == even_right.status_code == 429
    assert blocked.headers["retry-after"] == str(settings.throttle_window_seconds)
    assert blocked.json()["detail"]["code"] == "throttled"
    assert verifies == []  # refused before any password hashing
    assert failures(db_session) == before  # and nothing was written: hammering cannot extend the block


def test_an_unknown_account_is_throttled_exactly_like_a_real_one(session_client, db_session):
    statuses = [fail(session_client, "ghost@tests.invalid", "198.51.100.2").status_code for _ in range(settings.throttle_pair_max_failures + 2)]
    assert statuses == [401] * settings.throttle_pair_max_failures + [429, 429]


def test_another_source_is_not_affected_by_the_pair_limit(session_client, db_session):
    user = login_user(db_session)
    for _ in range(settings.throttle_pair_max_failures + 3):
        fail(session_client, user.email, "198.51.100.1")
    assert login(session_client, user.email, PASSWORD, headers=src("198.51.100.77")).status_code == 200


# --- per source -------------------------------------------------------------------------------------------------------------------------------------


def test_the_source_limit_stops_password_spraying_across_accounts(session_client, db_session, monkeypatch):
    set_limits(monkeypatch, throttle_source_max_failures=6)
    codes = [fail(session_client, f"victim{i}@tests.invalid", "203.0.113.5").status_code for i in range(10)]
    assert codes == [401] * 6 + [429] * 4
    assert failures(db_session) == 6  # the rows an attacker can cause from one source are capped by the limit
    assert login(session_client, login_user(db_session).email, PASSWORD, headers=src("203.0.113.6")).status_code == 200


# --- per identifier across sources ---------------------------------------------------------------------------------------------------------------


def test_distributed_guessing_is_bounded_but_a_known_source_is_never_locked_out(session_client, db_session, monkeypatch):
    set_limits(monkeypatch, throttle_pair_max_failures=3, throttle_identifier_max_failures=6)
    victim = login_user(db_session)
    assert login(session_client, victim.email, PASSWORD, headers=src("192.0.2.10")).status_code == 200  # the victim's usual address

    for address in ("198.51.100.21", "198.51.100.22", "198.51.100.23"):  # a botnet: 3 guesses from each of 3 sources
        for _ in range(3):
            fail(session_client, victim.email, address)

    # Another unknown source is now refused for this identifier ...
    assert fail(session_client, victim.email, "198.51.100.24").status_code == 429
    assert login(session_client, victim.email, PASSWORD, headers=src("198.51.100.25")).status_code == 429
    # ... but the attacker's failures cannot lock the victim out of the source they have logged in from.
    assert login(session_client, victim.email, PASSWORD, headers=src("192.0.2.10")).status_code == 200


def test_the_identifier_block_ends_when_the_failures_leave_the_window_and_cannot_be_extended(session_client, db_session, monkeypatch):
    set_limits(monkeypatch, throttle_pair_max_failures=3, throttle_identifier_max_failures=4)
    victim = login_user(db_session)
    start = clock.utcnow()
    monkeypatch.setattr(clock, "utcnow", lambda: start)
    for address in ("198.51.100.31", "198.51.100.32"):
        for _ in range(2):
            fail(session_client, victim.email, address)
    before = failures(db_session)

    # The attacker keeps trying for most of the window: every attempt is refused and writes nothing.
    for minute in range(1, 14):
        monkeypatch.setattr(clock, "utcnow", lambda minute=minute: start + timedelta(minutes=minute))
        assert fail(session_client, victim.email, "198.51.100.33").status_code == 429
    assert failures(db_session) == before

    # The block ends because of the ORIGINAL failures leaving the window, not later than that.
    monkeypatch.setattr(clock, "utcnow", lambda: start + timedelta(seconds=settings.throttle_window_seconds + 1))
    assert login(session_client, victim.email, PASSWORD, headers=src("198.51.100.34")).status_code == 200


def test_a_user_on_a_new_source_can_log_in_again_once_the_window_has_passed(session_client, db_session, monkeypatch):
    set_limits(monkeypatch, throttle_pair_max_failures=2, throttle_identifier_max_failures=2)
    victim = login_user(db_session)
    start = clock.utcnow()
    monkeypatch.setattr(clock, "utcnow", lambda: start)
    fail(session_client, victim.email, "198.51.100.41")
    fail(session_client, victim.email, "198.51.100.42")
    assert login(session_client, victim.email, PASSWORD, headers=src("198.51.100.43")).status_code == 429
    monkeypatch.setattr(clock, "utcnow", lambda: start + timedelta(seconds=settings.throttle_window_seconds + 5))
    assert login(session_client, victim.email, PASSWORD, headers=src("198.51.100.43")).status_code == 200


def test_there_is_no_account_level_state_to_lock(db_session):
    columns = db_session.execute(text("select column_name from information_schema.columns where table_name = 'user_credentials'")).scalars().all()
    assert not {"failed_count", "throttled_until", "locked_until", "failed_attempts"} & set(columns)


# --- the global valve ---------------------------------------------------------------------------------------------------------------------------


def test_the_valve_stops_recording_failures_and_refuses_unknown_sources_but_not_known_ones(session_client, db_session, monkeypatch):
    set_limits(monkeypatch, throttle_global_max_failure_events=5, throttle_pair_max_failures=50, throttle_source_max_failures=50)
    victim = login_user(db_session)
    assert login(session_client, victim.email, PASSWORD, headers=src("192.0.2.50")).status_code == 200
    for i in range(5):
        fail(session_client, f"x{i}@tests.invalid", f"198.51.100.{60 + i}")
    assert failures(db_session) == 5

    # Valve open: an unknown source is refused cheaply; a source that has logged in before still gets through.
    assert fail(session_client, victim.email, "198.51.100.99").status_code == 429
    assert failures(db_session) == 5
    assert fail(session_client, victim.email, "192.0.2.50").status_code == 401  # known source: verified, but the failure is NOT recorded
    assert failures(db_session) == 5
    assert login(session_client, victim.email, PASSWORD, headers=src("192.0.2.50")).status_code == 200


# --- the table cannot grow without bound ------------------------------------------------------------------------------------------------------------


def test_one_source_cannot_cause_more_failure_rows_than_its_limit_whatever_it_tries(session_client, db_session, monkeypatch):
    set_limits(monkeypatch, throttle_source_max_failures=8)
    for i in range(60):
        fail(session_client, f"random-{i}@tests.invalid", "203.0.113.9")
    assert failures(db_session) == 8


def test_events_have_a_fixed_small_shape(db_session):
    columns = {
        row.column_name: row.character_maximum_length
        for row in db_session.execute(text("select column_name, character_maximum_length from information_schema.columns where table_name = 'security_events'"))
    }
    assert columns["source"] == 64 and columns["identifier_hash"] == 64 and columns["detail"] == 64 and columns["event_type"] == 40
    assert set(columns) == {"id", "occurred_at", "event_type", "actor_user_id", "organization_id", "source", "identifier_hash", "detail"}


def test_every_throttle_count_is_a_limited_index_scan(db_session):
    from sqlalchemy.dialects import postgresql

    from app.core.security_events import count_recent  # noqa: F401  (the function under test)

    indexes = set(db_session.execute(text("select indexname from pg_indexes where tablename = 'security_events'")).scalars())
    assert {"ix_security_events_type_source_time", "ix_security_events_type_identifier_time", "ix_security_events_type_time"} <= indexes

    from sqlalchemy import func, select

    from app.models.auth import SecurityEvent

    limited = select(SecurityEvent.id).where(SecurityEvent.event_type == "login_failure").limit(30).subquery()
    sql = str(select(func.count()).select_from(limited).compile(dialect=postgresql.dialect()))
    assert "LIMIT" in sql

    statements = []
    from sqlalchemy import event as sa_event

    connection = db_session.connection()

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    sa_event.listen(connection, "before_cursor_execute", capture)
    try:
        now = clock.utcnow()
        security_events.count_recent(db_session, "login_failure", now - timedelta(minutes=15), 30, source="1.2.3.4", ident_hash="a" * 64)
    finally:
        sa_event.remove(connection, "before_cursor_execute", capture)
    assert statements and all("LIMIT" in s for s in statements)


# --- the source key ---------------------------------------------------------------------------------------------------------------------------------


def request_with(headers: dict[str, str], peer: str | None = "10.0.0.1", authenticated: bool = True) -> Request:
    scope = {"type": "http", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()], "client": (peer, 1234) if peer else None, internal_auth.AUTHENTICATED_KEY: authenticated}
    return Request(scope)


@pytest.mark.parametrize(
    "header, expected",
    [
        ("198.51.100.7", "198.51.100.7"),
        (" 198.51.100.7 , 10.9.9.9", "198.51.100.7"),  # only the first entry
        ("2001:db8:1:2:3:4:5:6", "2001:db8:1:2::/64"),  # a v6 client is keyed by its /64
        ("2001:db8:1:2:ffff:ffff:ffff:ffff", "2001:db8:1:2::/64"),
        ("not-an-ip", "unknown"),
        ("", "unknown"),
    ],
)
def test_the_source_comes_from_the_client_ip_header_when_trusted(header, expected):
    assert security_events.client_source(request_with({"X-Client-Ip": header})) == expected


def test_the_header_is_ignored_unless_trusted(monkeypatch):
    monkeypatch.setattr(settings, "trust_client_ip_header", False)
    assert security_events.client_source(request_with({"X-Client-Ip": "198.51.100.7"}, peer="10.0.0.1")) == "10.0.0.1"
    assert security_events.client_source(request_with({"X-Client-Ip": "198.51.100.7"}, peer=None)) == "unknown"


def test_a_missing_header_falls_back_to_the_peer_address():
    assert security_events.client_source(request_with({}, peer="10.0.0.1")) == "10.0.0.1"


def test_throttling_state_is_in_events_only(session_client, db_session):
    user = login_user(db_session)
    fail(session_client, user.email, "198.51.100.1")
    assert {e.event_type for e in events(db_session)} == {"login_failure"}
