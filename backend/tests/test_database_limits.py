"""The database limits: a bounded pool and server-side timeouts on every runtime connection, and a saturated pool answered
with a fast 503 instead of a hang or a 500. These run against the test database with deliberately tiny limits."""

import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import sessionmaker

from app.core import db, readiness
from app.core.config import Settings, settings
from app.core.db import make_engine
from app.main import app


def engine_with(**changes):
    """An engine like the runtime one, with some limits changed (a copy of the settings; nothing global is touched)."""
    engine = make_engine(settings.database_url, settings.model_copy(update=changes))
    return engine


def sqlstate(error: Exception) -> str | None:
    return getattr(getattr(error, "orig", None), "sqlstate", None)


def test_every_runtime_connection_carries_the_three_server_side_timeouts():
    with db.engine.connect() as connection:
        values = dict(
            connection.execute(
                text("select name, setting::bigint from pg_settings where name in ('statement_timeout', 'lock_timeout', 'idle_in_transaction_session_timeout')")
            ).all()
        )
    assert values == {
        "statement_timeout": settings.db_statement_timeout_ms,
        "lock_timeout": settings.db_lock_timeout_ms,
        "idle_in_transaction_session_timeout": settings.db_idle_in_transaction_timeout_ms,
    }


def test_the_pool_is_the_configured_finite_size():
    pool = db.engine.pool
    assert (pool.size(), pool._max_overflow, pool._timeout) == (settings.db_pool_size, settings.db_max_overflow, settings.db_pool_timeout_seconds)


def test_a_statement_that_runs_too_long_is_cancelled_by_the_server():
    engine = engine_with(db_statement_timeout_ms=200)
    started = time.monotonic()
    with pytest.raises(OperationalError) as caught, engine.connect() as connection:
        connection.execute(text("select pg_sleep(5)"))
    assert sqlstate(caught.value) == "57014"  # query_canceled
    assert time.monotonic() - started < 3
    engine.dispose()


def test_a_lock_wait_that_lasts_too_long_fails_instead_of_waiting():
    engine = engine_with(db_lock_timeout_ms=200)
    holder = engine.connect()
    try:
        holder.execute(text("select pg_advisory_lock(7_000_001)"))
        started = time.monotonic()
        with pytest.raises(OperationalError) as caught, engine.connect() as waiter:
            # Safety net so a missing lock timeout FAILS this test (57014) instead of waiting forever.
            waiter.execute(text("set statement_timeout = 4000"))
            waiter.execute(text("select pg_advisory_lock(7_000_001)"))
        assert sqlstate(caught.value) == "55P03"  # lock_not_available
        assert time.monotonic() - started < 3
    finally:
        holder.execute(text("select pg_advisory_unlock_all()"))
        holder.close()
        engine.dispose()


def test_a_session_left_idle_inside_a_transaction_is_ended_by_the_server():
    engine = engine_with(db_idle_in_transaction_timeout_ms=500)
    connection = engine.connect()
    try:
        connection.execute(text("select 1"))  # begins a transaction, then nothing happens
        time.sleep(1.5)
        with pytest.raises((OperationalError, InterfaceError)):
            connection.execute(text("select 1"))
    finally:
        connection.close()
        engine.dispose()


def test_an_exhausted_pool_refuses_quickly_and_recovers_when_a_connection_returns():
    engine = engine_with(db_pool_size=1, db_max_overflow=0, db_pool_timeout_seconds=0.3)
    first = engine.connect()
    try:
        started = time.monotonic()
        with pytest.raises(PoolTimeoutError):
            engine.connect()
        assert time.monotonic() - started < 2
    finally:
        first.close()
    with engine.connect() as again:  # no restart needed
        assert again.execute(text("select 1")).scalar() == 1
    engine.dispose()


def test_a_session_that_fails_still_returns_its_connection_to_the_pool():
    engine = engine_with(db_pool_size=1, db_max_overflow=0, db_pool_timeout_seconds=0.3)
    factory = sessionmaker(bind=engine)
    for _ in range(3):  # a leaked connection would make the second round time out
        with pytest.raises(RuntimeError), factory() as session:
            session.execute(text("select 1"))
            raise RuntimeError("request failed")
    assert engine.pool.checkedout() == 0
    engine.dispose()


def test_a_saturated_pool_answers_503_overloaded_with_retry_after_not_a_500():
    def exhausted():
        raise PoolTimeoutError("QueuePool limit of size 12 overflow 4 reached")
        yield  # pragma: no cover  (makes this a generator dependency like get_db)

    app.dependency_overrides[db.get_db] = exhausted
    try:
        response = TestClient(app, raise_server_exceptions=False).get("/api/customers")
    finally:
        app.dependency_overrides.pop(db.get_db, None)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.json() == {"detail": {"code": "overloaded", "message": "The service is busy. Try again in a moment."}}


def test_readiness_names_a_saturated_pool_instead_of_calling_it_unexpected():
    engine = engine_with(db_pool_size=1, db_max_overflow=0, db_pool_timeout_seconds=0.3)
    held = engine.connect()
    try:
        assert readiness.evaluate(engine, ("some_head",)) == "pool_exhausted"
    finally:
        held.close()
        engine.dispose()


@pytest.mark.parametrize(
    "name,value",
    [
        ("db_pool_size", 0),
        ("db_max_overflow", -1),
        ("db_pool_timeout_seconds", 0),
        ("db_statement_timeout_ms", 0),
        ("db_lock_timeout_ms", 0),
        ("db_idle_in_transaction_timeout_ms", 0),
    ],
)
def test_the_limits_are_validated(name, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="postgresql+psycopg://x", **{name: value})
