"""Disposable databases and roles on the TEST server, for the deployment tests (readiness, the migration job, roles).

Everything created here lives on the dedicated test server (a separate Postgres on tmpfs, see docker-compose.yml) under a
random name that ends in `_test`, and is dropped afterwards. The development database is never touched.
"""

import os
import secrets
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.scripts import reset_test_db

BACKEND_DIR = Path(__file__).resolve().parents[1]
MIGRATE = [sys.executable, "-m", "app.scripts.migrate"]


def admin_url() -> str:
    """The test server's privileged URL (the same one pytest and Playwright use)."""
    return reset_test_db.test_database_url()


def url_for(database: str, *, user: str | None = None, password: str | None = None) -> str:
    url = make_url(admin_url()).set(database=database)
    if user is not None:
        url = url.set(username=user, password=password)
    return url.render_as_string(hide_password=False)


def psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


@contextmanager
def disposable_database(prefix: str = "d2") -> Iterator[str]:
    """An empty database (no schema) on the test server; yields its privileged URL."""
    name = f"{prefix}_{secrets.token_hex(4)}_test"
    url = url_for(name)
    reset_test_db.assert_is_test_database(url)
    admin = create_engine(admin_url(), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield url
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@contextmanager
def disposable_roles(*names: str) -> Iterator[dict[str, str]]:
    """Role names (suffixed to be unique) mapped to fresh passwords; the roles are dropped (with what they own) afterwards.
    The roles are created by `bootstrap_roles`, not here."""
    suffix = secrets.token_hex(3)
    roles = {name: f"{name}_{suffix}" for name in names}
    passwords = {name: secrets.token_hex(12) for name in names}
    try:
        yield {"names": roles, "passwords": passwords}  # type: ignore[misc]
    finally:
        admin = create_engine(admin_url(), isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            for role in roles.values():
                connection.execute(text(f'DROP OWNED BY "{role}"'))  # (a no-op for a role that owns nothing)
                connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        admin.dispose()


def run_migrate(url: str, *args: str, env: dict[str, str] | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    """The migration job as a real separate process, with ONLY the migration credentials it needs in its environment."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DATABASE_URL", "MIGRATION_DATABASE_URL", "BFF_INTERNAL_SECRET", "SECURITY_KEY", "RUNTIME_DB_ROLE", "APP_ENV"}
    }
    # development by default (a runtime role is then optional); tests of the production rules pass their own `env`
    environment.update({"MIGRATION_DATABASE_URL": url, "APP_ENV": "development"})
    environment.update(env or {})
    return subprocess.run(
        [*MIGRATE, *args], cwd=BACKEND_DIR, env=environment, capture_output=True, text=True, timeout=timeout, check=False
    )


def scalar(url: str, statement: str, **params):
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            return connection.execute(text(statement), params).scalar()
    finally:
        engine.dispose()


def execute(url: str, statement: str, **params) -> None:
    scalar(url, statement, **params) if statement.lstrip().lower().startswith("select") else _run(url, statement, params)


def _run(url: str, statement: str, params: dict) -> None:
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text(statement), params)
    finally:
        engine.dispose()
