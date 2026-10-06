"""Test execution can never modify the development database."""

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.core.db import engine
from app.scripts import reset_test_db
from app.scripts.reset_test_db import UnsafeDatabase, assert_is_test_database

DEV = "postgresql+psycopg://user:pw@localhost:5432/business_platform"


def development_urls() -> tuple[str, ...]:
    """The developer's development URL(s) from .env, or, where none exists (CI has no development database), a synthetic one,
    so the guard is still exercised against a development-shaped URL."""
    return reset_test_db.DEVELOPMENT_DATABASE_URLS or (DEV,)


# --- what this pytest session is connected to ----------------------------------------------------


def test_the_application_engine_points_at_the_test_database():
    assert settings.database_url == reset_test_db.test_database_url()
    assert engine.url.database.endswith("_test")


def test_the_test_database_is_not_any_development_database():
    if not os.environ.get("CI"):
        assert reset_test_db.DEVELOPMENT_DATABASE_URLS, "the dev URL should be known from .env"  # (CI has no development database at all)
    for dev_url in development_urls():
        dev, test = make_url(dev_url), engine.url
        assert dev.database != test.database
        assert (dev.host, dev.port) != (test.host, test.port) or dev.database != test.database
        assert dev.port != test.port, "tests must use a physically separate server"


def test_the_session_database_was_rebuilt_from_migrations():
    with engine.connect() as connection:
        version = connection.execute(text("select version_num from alembic_version")).scalar_one()
    assert version  # a migrated schema, created by the session fixture


# --- the guard ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://u:p@localhost:5433/business_platform",  # no _test suffix
        "postgresql+psycopg://u:p@localhost:5432/business_platform",  # the dev database
        "postgresql+psycopg://u:p@localhost:5433/production",
        "postgresql+psycopg://u:p@localhost:5433/test_business_platform",  # suffix, not prefix
        "postgresql+psycopg://u:p@localhost:5433/",
    ],
)
def test_the_guard_refuses_databases_that_are_not_clearly_test_databases(url):
    with pytest.raises(UnsafeDatabase):
        assert_is_test_database(url, (DEV,))


def test_the_guard_refuses_the_development_database_even_if_it_is_named_like_a_test_database():
    dev_named_test = "postgresql+psycopg://u:p@localhost:5432/business_platform_test"

    with pytest.raises(UnsafeDatabase):
        assert_is_test_database(dev_named_test, (dev_named_test,))


def test_the_guard_refuses_a_test_named_database_on_the_development_server():
    # the name is fine, but the data would live in the same server as development
    with pytest.raises(UnsafeDatabase, match="different server"):
        assert_is_test_database("postgresql+psycopg://u:p@localhost:5432/other_test", (DEV,))
    with pytest.raises(UnsafeDatabase, match="different server"):
        assert_is_test_database("postgresql+psycopg://u:p@127.0.0.1:5432/other_test", ("postgresql+psycopg://u:p@127.0.0.1:5432/business_platform",))


def test_the_guard_accepts_a_separate_test_database():
    assert_is_test_database("postgresql+psycopg://u:p@localhost:5433/business_platform_test", (DEV,))


def test_destructive_setup_refuses_before_it_ever_connects(monkeypatch: pytest.MonkeyPatch):
    def never_connect(*args, **kwargs):
        raise AssertionError("tried to connect to a database that must be refused")

    monkeypatch.setattr(reset_test_db, "create_engine", never_connect)
    for dev_url in development_urls():
        with pytest.raises(UnsafeDatabase):
            reset_test_db.reset_schema(dev_url)
        with pytest.raises(UnsafeDatabase):
            reset_test_db.reset(dev_url, with_seed=True)


def test_the_reset_command_refuses_when_pointed_at_the_development_database(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(reset_test_db, "create_engine", lambda *a, **k: pytest.fail("connected"))
    monkeypatch.setattr(reset_test_db, "test_database_url", lambda env=None: development_urls()[0])

    assert reset_test_db.main(["--seed"]) == 2


def test_a_missing_test_database_url_is_an_error_not_a_fallback():
    with pytest.raises(UnsafeDatabase, match="TEST_DATABASE_URL"):
        reset_test_db.test_database_url({"DATABASE_URL": DEV})


# --- the databases are physically separate -----------------------------------------------------------------


def test_data_written_by_tests_never_appears_in_the_development_database():
    if not reset_test_db.DEVELOPMENT_DATABASE_URLS:
        pytest.skip("no development database is configured (CI has none): nothing to compare against")
    marker = f"isolation-canary-{uuid.uuid4().hex}"
    with engine.begin() as connection:  # committed, on the TEST server
        connection.execute(text("insert into organizations (name) values (:n)"), {"n": marker})
    try:
        dev_url = reset_test_db.DEVELOPMENT_DATABASE_URLS[0]
        dev_engine = create_engine(dev_url)
        try:
            with dev_engine.connect() as connection:
                found_in_dev = connection.execute(text("select count(*) from organizations where name = :n"), {"n": marker}).scalar_one()
        except Exception as exc:  # the dev server is simply not running
            pytest.skip(f"development database not reachable: {type(exc).__name__}")
        finally:
            dev_engine.dispose()
        assert found_in_dev == 0
        with engine.connect() as connection:
            assert connection.execute(text("select count(*) from organizations where name = :n"), {"n": marker}).scalar_one() == 1
    finally:
        with engine.begin() as connection:
            connection.execute(text("delete from organizations where name = :n"), {"n": marker})


def test_the_reset_command_needs_no_developer_env_file_or_app_env():
    """Playwright's global setup runs it with nothing but TEST_DATABASE_URL (a CI runner has no .env and no APP_ENV)."""
    import subprocess
    import sys

    from tests.db_support import BACKEND_DIR, disposable_database

    with disposable_database("reset") as url:
        environment = {k: v for k, v in os.environ.items() if k not in {"DATABASE_URL", "MIGRATION_DATABASE_URL", "APP_ENV", "AUTH_MODE"}}
        environment["TEST_DATABASE_URL"] = url
        result = subprocess.run([sys.executable, "-m", "app.scripts.reset_test_db", "--seed"], cwd=BACKEND_DIR, env=environment, capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, result.stderr[-1500:]
