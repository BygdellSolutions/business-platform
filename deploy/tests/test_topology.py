"""The production-like local stack (D1 topology + D2 orchestration): only the frontend is published; the backend and database
are private; migrations run in their own one-shot job under the owner role and the web process runs as the restricted app role
and migrates nothing; the browser path really reaches FastAPI and PostgreSQL; logs correlate and redact."""

import json
import re
import time
from collections.abc import Iterator

import httpx
import pytest

from conftest import BFF_SECRET, docker, get
from stack_support import Stack, repository_head


@pytest.fixture(scope="module")
def stack() -> Iterator[Stack]:
    s = Stack("topology")
    try:
        s.compose("build")
        s.compose("up", "-d", "frontend")  # postgres -> db-bootstrap -> migrate -> backend (healthy) -> frontend
        s.wait_ready()
        yield s
    finally:
        s.down()


def test_the_dependency_chain_ran_in_order_and_the_one_shot_jobs_succeeded(stack):
    bootstrap, migrate = stack.state("db-bootstrap"), stack.state("migrate")
    assert (bootstrap["Status"], bootstrap["ExitCode"]) == ("exited", 0)
    assert (migrate["Status"], migrate["ExitCode"]) == ("exited", 0)
    backend, frontend = stack.state("backend"), stack.state("frontend")
    # ordered by the clock: bootstrap finished, then migrate finished, then the backend started, then the frontend
    assert bootstrap["FinishedAt"] <= migrate["StartedAt"] <= migrate["FinishedAt"] <= backend["StartedAt"] <= frontend["StartedAt"]
    assert backend["Health"]["Status"] == "healthy"  # readiness, from the image's HEALTHCHECK
    assert frontend["Running"] and backend["Running"] and backend["RestartCount"] == 0


def test_the_web_process_never_migrates_only_the_job_does(stack):
    migrate_logs = stack.compose("logs", "--no-log-prefix", "migrate").stdout
    assert '"event":"migrated"' in migrate_logs and "Running upgrade" in migrate_logs
    backend_logs = stack.compose("logs", "--no-log-prefix", "backend").stdout
    assert not re.search(r"Running upgrade|alembic|migrat", backend_logs, re.IGNORECASE)
    assert stack.sql("select version_num from alembic_version") == repository_head()
    # the web image's process list has no migration in it either
    processes = stack.compose("exec", "-T", "backend", "sh", "-c", "cat /proc/*/cmdline | tr '\\0' ' '").stdout
    assert "app.scripts.migrate" not in processes and "alembic" not in processes


def test_only_the_frontend_is_published_to_the_host_and_only_on_loopback(stack):
    config = json.loads(stack.compose("config", "--format", "json").stdout)["services"]
    for private in ("backend", "postgres", "migrate", "db-bootstrap"):
        assert "ports" not in config[private], private
    assert [(p["host_ip"], p["target"]) for p in config["frontend"]["ports"]] == [("127.0.0.1", 3000)]
    for service in ("backend", "postgres", "frontend"):
        bindings = json.loads(docker("inspect", stack.container_id(service)).stdout)[0]["HostConfig"]["PortBindings"] or {}
        if service == "frontend":
            assert list(bindings) == ["3000/tcp"] and bindings["3000/tcp"][0]["HostIp"] == "127.0.0.1"
        else:
            assert bindings == {}, (service, bindings)


def test_the_frontend_reaches_the_private_backend_and_the_backend_demands_the_internal_secret(stack):
    probe = (
        "const call = (path, headers) => fetch('http://backend:8000' + path, {headers}).then(async r => r.status + ' ' + (await r.text()));"
        "Promise.all([call('/health'), call('/health/ready'), call('/api/me/user'), call('/api/me/user', {'x-bff-secret': 'wrong'}),"
        f" call('/api/me/user', {{'x-bff-secret': '{BFF_SECRET}'}})]).then(r => console.log(JSON.stringify(r)))"
    )
    health, ready, no_secret, wrong_secret, with_secret = json.loads(stack.compose("exec", "-T", "frontend", "node", "-e", probe).stdout)
    assert health == '200 {"status":"ok"}' and ready == '200 {"status":"ready"}'
    assert no_secret.startswith("403 ") and wrong_secret.startswith("403 ") and "internal_auth_failed" in no_secret
    assert with_secret.startswith("401 ")  # the application answered (no user): the guard let the BFF through
    # nothing but the frontend answers on the host
    with pytest.raises(httpx.HTTPError):
        httpx.Client(timeout=3).get(f"http://127.0.0.1:{stack.port + 1}/health")


def test_the_web_process_runs_as_the_restricted_app_role_and_cannot_change_the_schema(stack):
    script = (
        "import os, psycopg\n"
        "url = os.environ['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://')\n"
        "with psycopg.connect(url, autocommit=True) as c:\n"
        "    print(c.execute('select current_user, (select rolsuper or rolcreaterole or rolcreatedb from pg_roles where rolname = current_user)').fetchone())\n"
        "    for statement in ('create table sneaky (id int)', 'alter table organizations add column x int', 'drop table customers', 'create role sneaky', 'update alembic_version set version_num = \\'x\\''):\n"
        "        try:\n            c.execute(statement); print('ALLOWED', statement)\n"
        "        except psycopg.errors.InsufficientPrivilege:\n            print('denied', statement)\n"
    )
    out = stack.compose("exec", "-T", "backend", "python", "-c", script).stdout
    assert "('bp_app', False)" in out and "ALLOWED" not in out and out.count("denied") == 5
    assert stack.sql("select rolname from pg_roles where rolname in ('bp_app','bp_owner') order by 1") == "bp_app\nbp_owner"
    assert stack.sql("select tableowner from pg_tables where tablename = 'customers'") == "bp_owner"


def test_a_browser_request_really_goes_browser_to_bff_to_fastapi_to_postgres_and_back(stack):
    """Bootstrap a user with the operator CLI (as the app role), redeem the setup link through the PUBLIC frontend, load a page
    whose server component asks FastAPI (which asks PostgreSQL) who the user is, and create an organization (many tables, a
    deferred trigger, a unique key) through the BFF with the app role."""
    out = stack.compose("exec", "-T", "backend", "python", "-m", "app.scripts.admin", "bootstrap-user", "--email", "operator@rehearsal.test", "--name", "Rehearsal Operator").stdout
    token = re.search(r"/setup#([A-Za-z0-9_-]{43})", out).group(1)
    stack.compose("exec", "-T", "backend", "python", "-m", "app.scripts.admin", "grant-org-creation", "--email", "operator@rehearsal.test")
    with httpx.Client(base_url=stack.base, timeout=20) as browser:
        pre = browser.get("/api/auth/pre")
        assert pre.status_code == 200
        response = browser.post("/api/auth/setup", json={"token": token, "password": "a rehearsal passphrase 123"}, headers={"origin": stack.base, "x-pre-auth": pre.json()["token"]})
        assert response.status_code == 200, response.text
        assert "bp_session" in browser.cookies
        home = browser.get("/")
        assert home.status_code == 200 and "operator@rehearsal.test" in home.text  # FastAPI resolved the session to the user
        assert browser.get("/dev-login").status_code == 404
        created = browser.post(
            "/api/organizations", json={"name": "Rehearsal Org", "default_currency": "SEK"},
            headers={"origin": stack.base, "x-csrf-token": browser.cookies["bp_csrf"]},
        )
        assert created.status_code == 201, created.text
    assert stack.sql("select count(*) from security_events where event_type = 'setup_redeemed'") == "1"
    assert stack.sql("select count(*) from organizations where name = 'Rehearsal Org'") == "1"


def test_logs_correlate_one_request_id_across_both_services_and_hold_no_secret_or_query(stack):
    sentinel_query, sentinel_password, sentinel_bearer = "SENTINEL_QUERY_9f2a", "SENTINEL-PASSWORD-9f2a", "SENTINELBEARER9f2a1c4e7b0d3a6f8c1e4b7d0a3f6c9e2b5d8a"
    with httpx.Client(base_url=stack.base, timeout=20) as browser:
        pre = browser.get("/api/auth/pre")
        failed = browser.post(
            f"/api/auth/login?token={sentinel_query}", json={"email": "nobody@rehearsal.test", "password": sentinel_password},
            headers={"origin": stack.base, "x-pre-auth": pre.json()["token"], "authorization": f"Bearer {sentinel_bearer}", "x-request-id": "browser-chosen-request-id-0123"},
        )
        assert failed.status_code == 401
        request_id = failed.headers["x-request-id"]
        assert re.fullmatch(r"[0-9a-f]{32}", request_id) and request_id != "browser-chosen-request-id-0123"
    time.sleep(1)
    frontend = [json.loads(line) for line in stack.compose("logs", "--no-log-prefix", "frontend").stdout.splitlines() if line.startswith("{")]
    backend = [json.loads(line) for line in stack.compose("logs", "--no-log-prefix", "backend").stdout.splitlines() if line.startswith("{")]
    bff_line = [line for line in frontend if line.get("request_id") == request_id and line["event"] == "request"]
    api_line = [line for line in backend if line.get("request_id") == request_id and line["event"] == "request"]
    assert len(bff_line) == 1 and bff_line[0]["path"] == "/api/auth/login" and bff_line[0]["status"] == 401
    assert len(api_line) == 1 and api_line[0]["path"] == "/api/auth/login" and api_line[0]["status"] == 401  # the SAME id in FastAPI's log
    everything = stack.compose("logs", "--no-log-prefix").stdout + stack.compose("logs", "--no-log-prefix").stderr
    for secret in (sentinel_query, sentinel_password, sentinel_bearer, BFF_SECRET, stack.passwords["app"], stack.passwords["owner"], stack.passwords["db"], "browser-chosen-request-id"):
        assert secret not in everything, secret
    assert not re.search(r"\$argon2|postgresql(\+psycopg)?://", everything)
    assert "HTTP/1.1" not in everything and "GET /health?" not in everything  # Uvicorn's own access log is silenced


def test_the_frontend_health_is_liveness_and_readiness_is_the_chain(stack):
    status, headers, body = get(f"{stack.base}/api/health")
    assert (status, json.loads(body)) == (200, {"status": "ok"}) and headers["cache-control"] == "no-store"
    status, _, body = get(f"{stack.base}/api/ready")
    assert (status, json.loads(body)) == (200, {"status": "ready"})
    assert stack.state("frontend")["Health"]["Status"] == "healthy"  # the image's liveness check
    assert not re.search(r"postgres|alembic|revision|backend", body, re.IGNORECASE)


def test_the_development_rehearsal_sends_the_baseline_headers_and_no_hsts(stack):
    for path in ("/login", "/api/health"):
        _, headers, _ = get(f"{stack.base}{path}")
        assert headers["x-content-type-options"] == "nosniff" and headers["x-frame-options"] == "DENY"
        assert "strict-transport-security" not in headers and "x-powered-by" not in headers
    assert get(f"{stack.base}/login")[1]["referrer-policy"] == "no-referrer"
