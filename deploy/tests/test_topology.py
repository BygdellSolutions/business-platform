"""The production-like local stack: only the frontend is published; the backend and database are private; the web
process migrates nothing; the browser path really reaches FastAPI and PostgreSQL."""

import json
import os
import re
import secrets
import subprocess
import time
from collections.abc import Iterator

import httpx
import pytest

from conftest import BACKEND_DIR, COMPOSE_FILE, ROOT, RUN, docker, free_port, wait_http

PROJECT = f"bp-d1-topology-{RUN}"
HEAD = re.search(r"^revision(?::\s*str)?\s*=\s*['\"]([0-9a-f]+)['\"]", "", re.M)  # (replaced below from the repository)


def repository_head() -> str:
    """The single Alembic head according to the repository's migration files."""
    revisions, parents = {}, set()
    for path in (BACKEND_DIR / "alembic" / "versions").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        revision = re.search(r"^revision[^=]*=\s*['\"]([0-9a-f]+)['\"]", text, re.M).group(1)
        down = re.search(r"^down_revision[^=]*=\s*(None|['\"]([0-9a-f]+)['\"])", text, re.M).group(2)
        revisions[revision] = down
        if down:
            parents.add(down)
    heads = set(revisions) - parents
    assert len(heads) == 1, heads
    return heads.pop()


class Stack:
    def __init__(self):
        self.port = free_port()
        self.password = secrets.token_hex(12)
        self.env = {
            **os.environ,
            "REHEARSAL_PROJECT": PROJECT,
            "REHEARSAL_PORT": str(self.port),
            "REHEARSAL_DB_PASSWORD": self.password,
            "REHEARSAL_SECURITY_KEY": secrets.token_hex(24),
            "REHEARSAL_BACKEND_IMAGE": f"bp-d1-topology-backend-{RUN}",
            "REHEARSAL_FRONTEND_IMAGE": f"bp-d1-topology-frontend-{RUN}",
        }

    def compose(self, *args: str, check: bool = True, timeout: int = 900) -> subprocess.CompletedProcess:
        result = subprocess.run(["docker", "compose", "-f", str(COMPOSE_FILE), *args], env=self.env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        if check and result.returncode != 0:
            raise AssertionError(f"compose {' '.join(args)} failed:\n{result.stdout[-2000:]}\n{result.stderr[-3000:]}")
        return result

    def sql(self, query: str) -> str:
        return self.compose("exec", "-T", "postgres", "psql", "-U", "bp", "-d", "bp", "-At", "-c", query).stdout.strip()


@pytest.fixture(scope="module")
def stack() -> Iterator[Stack]:
    s = Stack()
    try:
        s.compose("build")
        s.compose("up", "-d", "--wait", "postgres")
        s.compose("up", "-d", "backend")
        time.sleep(4)  # let the web process start and settle BEFORE any migration is run by hand
        s.before_migration = s.sql("select coalesce(to_regclass('public.alembic_version')::text, 'none')")
        s.web_logs_before_migration = s.compose("logs", "backend").stdout
        s.compose("run", "--rm", "--no-deps", "backend", "python", "-m", "alembic", "upgrade", "head")
        s.compose("up", "-d", "frontend")
        wait_http(s.port, "/login", timeout=60)
        yield s
    finally:
        s.compose("down", "-v", "--remove-orphans", check=False)
        for image in (s.env["REHEARSAL_BACKEND_IMAGE"], s.env["REHEARSAL_FRONTEND_IMAGE"]):
            docker("rmi", "-f", image, check=False)


def test_the_web_process_never_migrates(stack):
    assert stack.before_migration == "none"  # the backend had been serving for seconds and the schema did not exist
    assert not re.search(r"Running upgrade|alembic", stack.web_logs_before_migration, re.IGNORECASE)
    assert stack.sql("select version_num from alembic_version") == repository_head()  # only the explicit step did it


def test_only_the_frontend_is_published_to_the_host_and_only_on_loopback(stack):
    config = json.loads(stack.compose("config", "--format", "json").stdout)["services"]
    assert "ports" not in config["backend"] and "ports" not in config["postgres"]
    assert [(p["host_ip"], p["target"]) for p in config["frontend"]["ports"]] == [("127.0.0.1", 3000)]

    for service in ("backend", "postgres", "frontend"):
        identifier = stack.compose("ps", "-q", service).stdout.strip()
        bindings = json.loads(docker("inspect", identifier).stdout)[0]["HostConfig"]["PortBindings"] or {}
        if service == "frontend":
            assert list(bindings) == ["3000/tcp"] and bindings["3000/tcp"][0]["HostIp"] == "127.0.0.1"
        else:
            assert bindings == {}, (service, bindings)
            assert docker("port", identifier, check=False).stdout.strip() == ""


def test_the_frontend_reaches_the_backend_and_the_backend_reaches_the_database_privately(stack):
    out = stack.compose("exec", "-T", "frontend", "node", "-e", "fetch('http://backend:8000/health').then(r=>r.text()).then(t=>console.log(t))").stdout
    assert '"status":"ok"' in out
    out = stack.compose(
        "exec", "-T", "backend", "python", "-c",
        "import os, psycopg\nurl = os.environ['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://')\n"
        "with psycopg.connect(url) as c: print(c.execute('select version_num from alembic_version').fetchone()[0])",
    ).stdout.strip()
    assert out == repository_head()
    # the backend does not answer on the host: nothing of the stack but the frontend is bound there
    other = httpx.Client(timeout=3)
    with pytest.raises(httpx.HTTPError):
        other.get(f"http://127.0.0.1:{stack.port + 1}/health")  # a neighbouring port: nothing listens


def test_a_browser_request_really_goes_browser_to_bff_to_fastapi_to_postgres(stack):
    """Bootstrap a user with the operator CLI, redeem the setup link through the PUBLIC frontend, and load a page whose
    server component asks FastAPI (which asks PostgreSQL) who the user is."""
    out = stack.compose("exec", "-T", "backend", "python", "-m", "app.scripts.admin", "bootstrap-user", "--email", "operator@rehearsal.test", "--name", "Rehearsal Operator").stdout
    token = re.search(r"/setup#([A-Za-z0-9_-]{43})", out).group(1)
    base = f"http://127.0.0.1:{stack.port}"
    with httpx.Client(base_url=base, timeout=20) as browser:
        pre = browser.get("/api/auth/pre")
        assert pre.status_code == 200
        response = browser.post("/api/auth/setup", json={"token": token, "password": "a rehearsal passphrase 123"}, headers={"origin": base, "x-pre-auth": pre.json()["token"]})
        assert response.status_code == 200, response.text
        assert "bp_session" in browser.cookies
        home = browser.get("/")
        assert home.status_code == 200 and "operator@rehearsal.test" in home.text  # FastAPI resolved the session to the user
        assert browser.get("/dev-login").status_code == 404
    assert stack.sql("select count(*) from security_events where event_type = 'setup_redeemed'") == "1"
