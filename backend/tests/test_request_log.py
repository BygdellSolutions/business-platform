"""The logging policy: one safe JSON line per request, a bounded request id nobody can inject into, no secret in any
log, and response headers for the backend's own (non-browser) answers."""

import io
import json
import logging
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.core.config import settings
from app.core.logging_config import JsonFormatter, configure_logging
from app.core.request_context import RequestContextMiddleware
from app.main import app
from tests.auth_support import BFF_SECRET, PASSWORD, login_user
from tests.db_support import BACKEND_DIR

FIELDS = {"ts", "level", "service", "event", "request_id", "method", "path", "status", "duration_ms"}
SENTINELS = {
    "password": "SENTINEL-PASSWORD-9f2a",
    "bearer": "SENTINELBEARERTOKEN9f2a1c4e7b0d3a6f8c1e4b7d0a3f6c9e2b5d8a1c4e",
    "session": "SENTINELSESSIONCOOKIE9f2a1c4e7b0d3a6f8c1e4b7d0a3f6c9e2b5d8a1c4e",
    "csrf": "SENTINELCSRFTOKEN9f2a1c4e7b0d3a6f8c1e4b7d0a3f6c9e2b5d8a1c4e",
    "setup": "SENTINELSETUPTOKEN9f2a1c4e7b0d3a6f8c1e4b7d0a3f6c9e2b5d8a1c4e",
    "invite": "SENTINELINVITETOKEN9f2a1c4e7b0d3a6f8c1e4b7d0a3f6c9e2b5d8a1c4e",
    "query": "SENTINEL_QUERY_VALUE_9f2a",
}


@pytest.fixture
def logs():
    """Everything the `bp` loggers print, formatted exactly as production does, as raw text."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter("backend"))
    logger = logging.getLogger("bp")
    logger.addHandler(handler)
    try:
        yield stream
    finally:
        logger.removeHandler(handler)


def lines(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def request_lines(stream: io.StringIO) -> list[dict]:
    return [line for line in lines(stream) if line["event"] == "request"]


# --- the request line --------------------------------------------------------------------------------------------------------------------------


def test_one_request_one_line_with_exactly_the_approved_fields(logs):
    TestClient(app).get("/health", headers={"x-request-id": "abcdefgh12345678"})
    (line,) = request_lines(logs)
    assert set(line) == FIELDS
    assert (line["service"], line["level"], line["method"], line["path"], line["status"], line["request_id"]) == ("backend", "info", "GET", "/health", 200, "abcdefgh12345678")
    assert isinstance(line["duration_ms"], float) and line["ts"].endswith("+00:00")


def test_the_path_never_includes_the_query_string(logs):
    TestClient(app).get(f"/health?token={SENTINELS['query']}&next=/x")
    assert SENTINELS["query"] not in logs.getvalue() and "token=" not in logs.getvalue()
    assert request_lines(logs)[0]["path"] == "/health"


def test_a_server_error_is_logged_as_an_error_without_its_message_or_body(logs):
    async def boom(request):
        raise RuntimeError(f"boom with {SENTINELS['password']}")

    crashing = RequestContextMiddleware(Starlette(routes=[Route("/boom", boom, methods=["POST"])]))
    response = TestClient(crashing, raise_server_exceptions=False).post("/boom?a=1", content=SENTINELS["password"])
    assert response.status_code == 500
    (line,) = request_lines(logs)
    assert (line["status"], line["level"], line["path"]) == (500, "error", "/boom")
    assert SENTINELS["password"] not in logs.getvalue()


def test_a_very_long_path_is_bounded(logs):
    TestClient(app).get("/" + "a" * 5000)
    assert len(request_lines(logs)[0]["path"]) <= 200


def test_a_control_character_in_the_path_cannot_break_the_one_line_format(logs):
    TestClient(app).get("/x%0a%0d{\"event\":\"forged\"}")
    assert len(logs.getvalue().strip().splitlines()) == 1
    assert [line["event"] for line in lines(logs)] == ["request"]


# --- the request id ----------------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("sent", ["abcdefgh12345678", "A" * 64, "a-b_c-d_e-f_g-h_"])
def test_a_well_formed_id_is_kept_and_returned(sent, logs):
    response = TestClient(app).get("/health", headers={"x-request-id": sent})
    assert response.headers["x-request-id"] == sent and request_lines(logs)[0]["request_id"] == sent


@pytest.mark.parametrize("sent", ["short", "A" * 65, "A" * 5000, "has space in it 1234", 'quote"quote12345678', "semi;colon;12345678", "tab\tseparated123456", "ünïcödé-id-12345678", "../../etc/passwd1234", "{\"a\":1}12345678"])
def test_a_malformed_or_oversized_id_is_replaced_never_trusted_verbatim(sent, logs):
    response = TestClient(app).get("/health", headers={"x-request-id": sent.encode("utf-8")})  # bytes: some are not ASCII
    returned = response.headers["x-request-id"]
    assert returned != sent and len(returned) == 32 and returned.isalnum()
    assert sent not in logs.getvalue()  # whatever it was, it is not in the log
    assert request_lines(logs)[0]["request_id"] == returned


def test_every_request_without_an_id_gets_a_fresh_random_one(logs):
    client = TestClient(app)
    ids = {client.get("/health").headers["x-request-id"] for _ in range(20)}
    assert len(ids) == 20


# --- nothing secret is logged ------------------------------------------------------------------------------------------------------------------


def test_no_secret_in_any_request_reaches_the_log(logs, session_client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "bff_internal_secret", SecretStr(BFF_SECRET))
    user = login_user(db_session)
    client = TestClient(app, headers={settings.bff_internal_header: BFF_SECRET})
    from app.core.db import get_db

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        for path, body in (
            ("/api/auth/login", {"email": user.email, "password": SENTINELS["password"]}),
            ("/api/auth/setup", {"token": SENTINELS["setup"], "password": SENTINELS["password"]}),
            ("/api/invite/preview", {"token": SENTINELS["invite"]}),
            ("/api/invite/accept-new", {"token": SENTINELS["invite"], "password": SENTINELS["password"], "name": "N"}),
        ):
            client.post(
                f"{path}?token={SENTINELS['query']}",
                json=body,
                headers={
                    "authorization": f"Bearer {SENTINELS['bearer']}",
                    "cookie": f"bp_session={SENTINELS['session']}",
                    "x-csrf-token": SENTINELS["csrf"],
                },
            )
        client.get("/api/me/user", headers={"authorization": f"Bearer {SENTINELS['bearer']}"})
        TestClient(app).get("/api/me/user")  # a refused one (no secret)
    finally:
        app.dependency_overrides.clear()
    output = logs.getvalue()
    assert len(request_lines(logs)) >= 6
    for name, sentinel in SENTINELS.items():
        assert sentinel not in output, name
    assert BFF_SECRET not in output and PASSWORD not in output
    # no password hash, no header or body fields at all
    assert "argon2" not in output and "$argon2id" not in output
    assert all(set(line) <= FIELDS for line in request_lines(logs))


def test_the_database_error_handler_logs_a_class_name_not_the_driver_message(logs, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from app.core import db

    def fail(*args, **kwargs):
        raise OperationalError("select 1", {}, Exception(f"connection to server at 'secret-host' failed: password {SENTINELS['password']}"))

    monkeypatch.setattr(db.engine, "connect", fail)
    response = TestClient(app, raise_server_exceptions=False).get("/health/ready")
    assert response.status_code == 503  # readiness catches it itself
    assert SENTINELS["password"] not in logs.getvalue() and "secret-host" not in logs.getvalue()


# --- response headers of the backend ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/health", "/no/such/path", "/health/ready"])
def test_every_backend_response_has_the_minimal_headers(path):
    response = TestClient(app).get(path)
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "strict-transport-security" not in response.headers  # the backend never speaks to a browser over TLS


# --- the real server: Uvicorn's own access log must not duplicate or leak ---------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_a_real_uvicorn_prints_only_our_line_and_never_the_query_string():
    port = free_port()
    environment = {**__import__("os").environ, "APP_ENV": "development", "AUTH_MODE": "dev", "PYTHONUNBUFFERED": "1"}
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],  # NO --no-access-log: the app itself must silence it
        cwd=BACKEND_DIR, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
                break
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.3)
        urllib.request.urlopen(f"http://127.0.0.1:{port}/health?token={SENTINELS['query']}", timeout=5)
        time.sleep(0.5)
    finally:
        process.terminate()
        output = process.communicate(timeout=20)[0]
    assert SENTINELS["query"] not in output and "token=" not in output
    assert "GET /health?" not in output and "HTTP/1.1" not in output  # Uvicorn's access line is silenced
    request_lines_out = [json.loads(line) for line in output.splitlines() if line.startswith('{"ts"')]
    assert [line["path"] for line in request_lines_out if line["event"] == "request"].count("/health") >= 2


def test_configure_logging_is_idempotent_and_silences_uvicorn_access():
    stream = io.StringIO()
    configure_logging("backend", stream)
    configure_logging("backend", stream)
    assert len(logging.getLogger("bp").handlers) == 1
    logging.getLogger("uvicorn.access").warning("GET /secret?token=x HTTP/1.1")
    assert stream.getvalue() == ""
    configure_logging("backend")  # restore the stdout handler for the rest of the run
