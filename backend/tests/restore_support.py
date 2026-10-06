"""Disposable worlds for the backup, restore and verification tests (test server only).

`restore_world` is ONE representative database, built once per pytest session: the real roles (owner, restricted app, read-only
backup), the real migration job as the owner, and `tests.restore_fixture` filling it through the application AS THE RESTRICTED
RUNTIME ROLE (which also proves that role can do everything the fixture needs). Tests that must damage a database never touch it:
they work on `clone(...)`, a copy made with CREATE DATABASE ... TEMPLATE, and every database here is dropped afterwards.
"""

import json
import os
import secrets
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.scripts import reset_test_db
from app.scripts.bootstrap_roles import bootstrap
from tests.db_support import BACKEND_DIR, admin_url, disposable_database, disposable_roles, psycopg_url, run_migrate, url_for

FAKE_PG = Path(__file__).with_name("fake_pg.py")


def role_url(database: str, role: str, password: str) -> str:
    return url_for(database, user=role, password=password)


def _admin_execute(statement: str) -> None:
    engine = create_engine(admin_url(), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text(statement))
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def restore_world():
    with disposable_roles("owner", "app", "backup") as roles, disposable_database("fx") as admin:  # (the database goes first: it is owned by a role)
        names, passwords = roles["names"], roles["passwords"]
        bootstrap(
            psycopg_url(admin), owner_role=names["owner"], owner_password=passwords["owner"], app_role=names["app"], app_password=passwords["app"],
            backup_role=names["backup"], backup_password=passwords["backup"],
        )
        database = make_url(admin).database
        world = SimpleNamespace(
            admin=admin, database=database, owner=names["owner"], app=names["app"], backup=names["backup"],
            owner_url=role_url(database, names["owner"], passwords["owner"]),
            app_url=role_url(database, names["app"], passwords["app"]),
            backup_url=role_url(database, names["backup"], passwords["backup"]),
            passwords=passwords,
        )
        migrated = run_migrate(world.owner_url, env={"APP_ENV": "production", "RUNTIME_DB_ROLE": world.app})
        assert migrated.returncode == 0, migrated.stdout + migrated.stderr
        world.facts = build_fixture(world.app_url)
        yield world


def build_fixture(url: str) -> dict:
    """`tests.restore_fixture` as its own process, connected as the role the URL names."""
    environment = {key: value for key, value in os.environ.items() if key not in {"DATABASE_URL", "MIGRATION_DATABASE_URL", "BFF_INTERNAL_SECRET", "SECURITY_KEY"}}
    environment.update({"DATABASE_URL": url, "APP_ENV": "development", "AUTH_MODE": "dev", "PYTHONPATH": str(BACKEND_DIR)})
    result = subprocess.run([sys.executable, "-m", "tests.restore_fixture"], cwd=BACKEND_DIR, env=environment, capture_output=True, text=True, timeout=300, check=False)
    assert result.returncode == 0, result.stdout[-1500:] + result.stderr[-2500:]
    return json.loads([line for line in result.stdout.splitlines() if line.startswith("{") and '"org_a"' in line][-1])


@contextmanager
def clone(world, prefix: str = "cl"):
    """A throwaway copy of the fixture database (data, ownership and grants included) that a test may damage."""
    name = f"{prefix}_{secrets.token_hex(4)}_test"
    reset_test_db.assert_is_test_database(url_for(name))
    _admin_execute(f'CREATE DATABASE "{name}" TEMPLATE "{world.database}" OWNER "{world.owner}"')
    try:
        yield SimpleNamespace(
            database=name, admin=url_for(name),
            owner_url=role_url(name, world.owner, world.passwords["owner"]), app_url=role_url(name, world.app, world.passwords["app"]),
            backup_url=role_url(name, world.backup, world.passwords["backup"]),
        )
    finally:
        _admin_execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@contextmanager
def scratch_target(*, different_roles: bool = False, tag: str = "test", populated: bool = False, world=None):
    """An EMPTY scratch database named like a restore target. With `different_roles` the scratch environment has its own owner
    and app role names (the production restore case: the recovery environment's roles are not the source's)."""
    names = ("sowner", "sapp") if different_roles else ()
    with disposable_roles(*names) if names else _no_roles() as roles:
        name = f"rt_{secrets.token_hex(4)}_restore_{tag}"
        reset_test_db.assert_is_test_database(url_for(name))
        _admin_execute(f'CREATE DATABASE "{name}"')
        try:
            if different_roles:
                owner, app = roles["names"]["sowner"], roles["names"]["sapp"]
                owner_password, app_password = roles["passwords"]["sowner"], roles["passwords"]["sapp"]
            else:
                owner, app = world.owner, world.app
                owner_password, app_password = world.passwords["owner"], world.passwords["app"]
                _admin_execute(f'ALTER DATABASE "{name}" OWNER TO "{owner}"')
            if different_roles:
                bootstrap(psycopg_url(url_for(name)), owner_role=owner, owner_password=owner_password, app_role=app, app_password=app_password)
            else:
                _admin_execute(f'GRANT CONNECT ON DATABASE "{name}" TO "{app}"')
                _admin_execute_in(name, f'ALTER SCHEMA public OWNER TO "{owner}"')
                _admin_execute_in(name, f'GRANT USAGE ON SCHEMA public TO "{app}"')
            yield SimpleNamespace(
                database=name, admin=url_for(name), owner=owner, app=app,
                owner_url=role_url(name, owner, owner_password), app_url=role_url(name, app, app_password), passwords={"owner": owner_password, "app": app_password},
            )
        finally:
            _admin_execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _admin_execute_in(database: str, statement: str) -> None:
    engine = create_engine(url_for(database), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text(statement))
    finally:
        engine.dispose()


@contextmanager
def _no_roles():
    yield {}


TOOL_ENVIRONMENT_KEYS = {"PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "LANG", "USERPROFILE", "COMSPEC", "PATHEXT"}


def run_tool(module: str, *args: str, env: dict[str, str] | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    """An operator tool as its own process with ONLY the environment given (plus what a Python process needs to start): none of
    the test run's DATABASE_URL, so a tool that fell back to it would fail the test."""
    environment = {key: value for key, value in os.environ.items() if key in TOOL_ENVIRONMENT_KEYS}
    environment.update({"PYTHONPATH": str(BACKEND_DIR), **(env or {})})
    return subprocess.run([sys.executable, "-m", module, *args], cwd=BACKEND_DIR, env=environment, capture_output=True, text=True, timeout=timeout, check=False, encoding="utf-8", errors="replace")


def run_backup_files(world, directory: Path) -> SimpleNamespace:
    """A (fake-client) dump and its real manifest, made by the real backup tool against the fixture, for the restore guard tests."""
    result = run_tool(
        "app.scripts.backup", "--output", str(directory / "bp.dump"), "--label", "test",
        env={"BACKUP_DATABASE_URL": world.backup_url, **fake_pg_env()},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return SimpleNamespace(dump=directory / "bp.dump", manifest=directory / "bp.dump.manifest.json")


def fake_pg_env(mode_dump: str = "ok", mode_restore: str = "ok", *, log: Path | None = None, **extra: str) -> dict[str, str]:
    """BP_PG_DUMP / BP_PG_RESTORE pointing at tests/fake_pg.py, for exercising the tools' safety logic without PostgreSQL clients."""
    environment = {
        "BP_PG_DUMP": json.dumps([sys.executable, str(FAKE_PG), "dump"]),
        "BP_PG_RESTORE": json.dumps([sys.executable, str(FAKE_PG), "restore"]),
        "FAKE_PG_DUMP_MODE": mode_dump,
        "FAKE_PG_RESTORE_MODE": mode_restore,
    }
    if log is not None:
        environment["FAKE_PG_LOG"] = str(log)
    environment.update(extra)
    return environment
