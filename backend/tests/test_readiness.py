"""Readiness is ONE invariant: the database's Alembic revision == this image's single head. Anything else is unready, and
the answer is coarse (nothing about revisions, hosts or errors leaves the process)."""

import json
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from alembic import command
from app.core import db, migration, readiness
from app.core.logging_config import JsonFormatter
from app.main import app
from tests.db_support import disposable_database, execute, scalar

HEAD = migration.code_heads()[0]
PARENT = migration.script_directory().get_revision(HEAD).down_revision


def upgrade(url: str, revision: str = "head") -> None:
    config = migration.alembic_config()
    config.attributes["database_url"] = url
    command.upgrade(config, revision)


@pytest.fixture(scope="module")
def at_head():
    with disposable_database("ready") as url:
        upgrade(url)
        yield url


def engine_for(url: str):
    return create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 2})


def check(url: str) -> str:
    engine = engine_for(url)
    try:
        return readiness.check(engine)
    finally:
        engine.dispose()


def test_the_repository_has_exactly_one_head_and_a_parent():
    assert len(migration.code_heads()) == 1 and isinstance(PARENT, str)


def test_exactly_the_head_is_ready(at_head):
    assert check(at_head) == readiness.READY
    assert scalar(at_head, "select version_num from alembic_version") == HEAD


def test_a_database_without_a_revision_table_is_not_ready():
    with disposable_database("ready") as url:
        assert check(url) == "no_revision_table"  # and asking did not create one:
        assert scalar(url, "select to_regclass('alembic_version') is null") is True


def test_an_old_revision_is_not_ready():
    with disposable_database("ready") as url:
        upgrade(url, PARENT)
        assert scalar(url, "select version_num from alembic_version") == PARENT
        assert check(url) == "revision_mismatch"
        assert scalar(url, "select version_num from alembic_version") == PARENT  # readiness never migrates


def test_a_newer_or_unknown_revision_is_not_ready():
    with disposable_database("ready") as url:
        upgrade(url)
        execute(url, "update alembic_version set version_num = 'ffffffffffff'")
        assert check(url) == "revision_mismatch"  # NOT "a revision this code knows": an image older than the database is unready too


def test_several_revision_rows_are_not_ready_even_if_one_is_the_head():
    with disposable_database("ready") as url:
        upgrade(url)
        execute(url, "alter table alembic_version drop constraint alembic_version_pkc")
        execute(url, "insert into alembic_version (version_num) values (:v)", v=PARENT)
        assert scalar(url, "select count(*) from alembic_version") == 2
        assert check(url) == "revision_rows"


def test_an_empty_revision_table_is_not_ready():
    with disposable_database("ready") as url:
        upgrade(url)
        execute(url, "delete from alembic_version")
        assert check(url) == "revision_rows"


def test_a_malformed_revision_table_is_not_ready():
    with disposable_database("ready") as url:
        execute(url, "create table alembic_version (something_else int)")
        assert check(url) == "unexpected_state"


def test_an_unreachable_database_is_not_ready():
    engine = create_engine("postgresql+psycopg://nobody:nothing@127.0.0.1:1/none", connect_args={"connect_timeout": 2})
    try:
        assert readiness.check(engine) == "database_unreachable"
    finally:
        engine.dispose()


@pytest.mark.parametrize("heads", [(), ("a1", "b2")])
def test_zero_or_several_code_heads_are_not_ready(at_head, heads):
    engine = engine_for(at_head)
    try:
        assert readiness.evaluate(engine, heads) == "code_heads"  # even though the database is at one of them
    finally:
        engine.dispose()


def test_a_revision_the_code_does_not_expect_is_not_ready_whatever_the_database_holds(at_head):
    engine = engine_for(at_head)
    try:
        assert readiness.evaluate(engine, ("0123456789ab",)) == "revision_mismatch"
    finally:
        engine.dispose()


# --- the HTTP contract ------------------------------------------------------------------------------------------------------------------------


@pytest.fixture
def log_lines():
    import io

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter("backend"))
    logger = logging.getLogger("bp")
    logger.addHandler(handler)
    try:
        yield lambda: [json.loads(line) for line in stream.getvalue().splitlines()]
    finally:
        logger.removeHandler(handler)


def test_the_endpoint_says_ready_or_unready_and_nothing_else(at_head, monkeypatch, log_lines):
    client = TestClient(app)
    ready_engine = engine_for(at_head)
    monkeypatch.setattr(db, "engine", ready_engine)
    response = client.get("/health/ready")
    assert (response.status_code, response.json()) == (200, {"status": "ready"})
    assert response.headers["cache-control"] == "no-store"
    ready_engine.dispose()

    with disposable_database("ready") as url:
        upgrade(url, PARENT)
        stale = engine_for(url)
        monkeypatch.setattr(db, "engine", stale)
        response = client.get("/health/ready")
        stale.dispose()
        rendered = response.text + json.dumps(dict(response.headers))
        assert (response.status_code, response.json()) == (503, {"status": "unready"})
        for sensitive in (HEAD, PARENT, "alembic", url, "postgresql", "127.0.0.1", "revision"):
            assert sensitive not in rendered
        # the reason is for the operator's log only, and it is a code, never a revision
        not_ready = [line for line in log_lines() if line["event"] == "not_ready"]
        assert [line["reason"] for line in not_ready] == ["revision_mismatch"]
        assert HEAD not in json.dumps(not_ready) and PARENT not in json.dumps(not_ready)


def test_an_unreachable_database_gives_the_same_coarse_answer(monkeypatch):
    engine = create_engine("postgresql+psycopg://nobody:secretpassword@127.0.0.1:1/none", connect_args={"connect_timeout": 2})
    monkeypatch.setattr(db, "engine", engine)
    response = TestClient(app).get("/health/ready")
    engine.dispose()
    assert (response.status_code, response.json()) == (503, {"status": "unready"})
    assert "secretpassword" not in response.text and "127.0.0.1" not in response.text


def test_liveness_needs_no_database(monkeypatch):
    engine = create_engine("postgresql+psycopg://nobody:nothing@127.0.0.1:1/none", connect_args={"connect_timeout": 2})
    monkeypatch.setattr(db, "engine", engine)
    assert TestClient(app).get("/health").json() == {"status": "ok"}
    engine.dispose()


def test_the_old_detailed_database_endpoint_is_gone():
    assert TestClient(app).get("/health/db").status_code == 404


def test_readiness_is_not_a_migration(at_head):
    text_of_module = open(readiness.__file__, encoding="utf-8").read() + open(migration.__file__, encoding="utf-8").read()
    assert "command.upgrade" not in text_of_module and "create_all" not in text_of_module


# --- a hung database cannot pile up probes or stall liveness ---------------------------------------------------------------------------------------


def test_a_probe_that_never_returns_is_bounded_and_blocks_further_probes(monkeypatch):
    import threading

    release = threading.Event()
    started = []
    monkeypatch.setattr(readiness, "check", lambda engine: (started.append(1), release.wait(10), readiness.READY)[2])
    begun = __import__("time").monotonic()
    assert readiness.check_bounded(object(), timeout=0.3) == "check_timeout"  # gave up waiting, did not hang
    assert __import__("time").monotonic() - begun < 2
    assert readiness.check_bounded(object(), timeout=0.3) == "check_in_progress"  # the stuck probe is still running: no second thread
    assert len(started) == 1
    release.set()
    for _ in range(50):  # the stuck probe finishes, and the next one runs normally again
        if readiness.check_bounded(object(), timeout=1) == readiness.READY:
            break
        __import__("time").sleep(0.05)
    else:
        raise AssertionError("readiness never recovered after the stuck probe finished")


def test_liveness_is_async_so_it_never_waits_for_a_worker_thread():
    import inspect

    from app.api.health import health

    assert inspect.iscoroutinefunction(health)


def test_the_endpoint_uses_the_bounded_single_flight_probe_never_the_raw_check(monkeypatch):
    calls = []
    monkeypatch.setattr(readiness, "check_bounded", lambda engine: (calls.append("bounded"), readiness.READY)[1])
    monkeypatch.setattr(readiness, "check", lambda engine: (_ for _ in ()).throw(AssertionError("the endpoint called the unbounded check")))
    assert TestClient(app).get("/health/ready").status_code == 200 and calls == ["bounded"]
