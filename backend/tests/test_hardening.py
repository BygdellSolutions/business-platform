"""Small production-hardening facts that must not regress: parameters are hidden from SQL errors, CORS is absent unless
configured, the container runs Uvicorn the safe way, and nothing resurrects the removed diagnostics."""

import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.core import db, migration
from app.core.internal_auth import InternalAuthMiddleware
from app.core.request_context import RequestContextMiddleware
from app.main import app, configure_middleware
from tests.db_support import admin_url

BACKEND = Path(__file__).resolve().parents[1]
SENTINEL = "SENTINEL-BOUND-VALUE-4d1f"


def failure_text(engine) -> str:
    """The text of a failed statement that carries a bound value but whose DATABASE message does not echo it."""
    with pytest.raises(DBAPIError) as caught:
        with engine.connect() as connection:
            connection.execute(text("select :v from no_such_table_anywhere"), {"v": SENTINEL})
    return str(caught.value)


def test_the_control_an_engine_without_hide_parameters_does_echo_the_value():
    engine = create_engine(admin_url())
    try:
        assert SENTINEL in failure_text(engine)  # (non-vacuous: the sentinel WOULD appear)
    finally:
        engine.dispose()


def test_the_application_engine_hides_bound_values_from_sql_errors():
    assert SENTINEL not in failure_text(db.engine)
    assert "[parameters:" not in failure_text(db.engine)


def test_the_migration_engine_hides_bound_values_from_sql_errors():
    engine = migration.migration_engine(admin_url())
    try:
        assert SENTINEL not in failure_text(engine)
        assert "[parameters:" not in failure_text(engine)
    finally:
        engine.dispose()


def test_the_database_connection_attempt_is_bounded():
    connection = db.engine.raw_connection()
    try:
        assert connection.driver_connection.info.get_parameters()["connect_timeout"] == "5"
    finally:
        connection.close()


# --- CORS and middleware ------------------------------------------------------------------------------------------------------------------------


def installed(application: FastAPI) -> list[type]:
    return [m.cls for m in application.user_middleware]


def test_without_cors_origins_the_cors_middleware_is_not_installed_at_all():
    application = FastAPI()
    configure_middleware(application, [])
    assert installed(application) == [RequestContextMiddleware, InternalAuthMiddleware]  # outermost first


def test_with_a_development_origin_cors_sits_between_the_context_and_the_secret_check():
    application = FastAPI()
    configure_middleware(application, ["http://localhost:3000"])
    assert installed(application) == [RequestContextMiddleware, CORSMiddleware, InternalAuthMiddleware]


def test_the_real_application_has_the_context_and_secret_guards():
    assert installed(app)[0] is RequestContextMiddleware and installed(app)[-1] is InternalAuthMiddleware


# --- the container runs Uvicorn the safe way -------------------------------------------------------------------------------------------------------------


DOCKERFILE = (BACKEND / "Dockerfile").read_text(encoding="utf-8")


def test_the_container_command_is_one_worker_without_uvicorns_access_log_or_proxy_headers():
    command = re.search(r"^CMD (\[.*\])$", DOCKERFILE, re.M).group(1)
    for flag in ('"--workers", "1"', '"--no-access-log"', '"--no-proxy-headers"', '"--no-server-header"'):
        assert flag in command, flag
    assert "--reload" not in command


def test_the_container_health_check_is_readiness_not_liveness():
    check = re.search(r"^HEALTHCHECK .*?\n  CMD (\[.*\])$", DOCKERFILE, re.M | re.S).group(1)
    assert "/health/ready" in check


def test_the_container_never_migrates_by_itself():
    cmd_lines = [line for line in DOCKERFILE.splitlines() if line.startswith(("CMD", "ENTRYPOINT"))]
    assert all("alembic" not in line and "migrate" not in line for line in cmd_lines)


def test_no_detailed_database_diagnostic_endpoint_exists_anywhere():
    routes = set(app.openapi()["paths"])
    assert "/health/db" not in routes and {"/health", "/health/ready"} <= routes
    for path in (BACKEND / "app").rglob("*.py"):
        assert "health/db" not in path.read_text(encoding="utf-8"), path
