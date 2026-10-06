"""D2 at the image level: production configuration is enforced when the CONTAINER starts (and prints reasons, never values),
and a production frontend serves its headers, health endpoints and no secret."""

import json
import re
import time

import pytest

from conftest import BACKEND_ENV, BFF_SECRET, SECURITY_KEY, container, docker, get, host_port, inspect, wait_http
from test_frontend_image import PRODUCTION_RUNTIME, page, serve

SENTINEL_PASSWORD = "SENTINELDBPASSWORD9f2a"
DB_URL = f"postgresql+psycopg://appuser:{SENTINEL_PASSWORD}@database.invalid:5432/none"


def exits(image: str, env: dict[str, str], timeout: float = 40):
    """Start the image and wait for it to stop on its own; returns (exit code or None if still running, all output)."""
    with container(image, env, publish=3000, remove_on_exit=False) as identifier:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and inspect(identifier)["State"]["Running"]:
            time.sleep(0.5)
        state = inspect(identifier)["State"]
        logs = docker("logs", identifier, check=False)
        return (None if state["Running"] else state["ExitCode"]), logs.stdout + logs.stderr


# --- the backend refuses an unsafe production configuration, naming the rule and never a value --------------------------------------------------------------


UNSAFE_BACKEND = {
    "AUTH_MODE=dev": ({"AUTH_MODE": "dev"}, "AUTH_MODE=dev is only allowed"),
    "AUTH_MODE=disabled": ({"AUTH_MODE": "disabled"}, "requires AUTH_MODE=session"),
    "DEV_USER_EMAIL": ({"DEV_USER_EMAIL": "dev@example.test"}, "DEV_USER_EMAIL must not be configured"),
    "TEST_DATABASE_URL": ({"TEST_DATABASE_URL": "postgresql+psycopg://x/y_test"}, "TEST_DATABASE_URL must not be configured"),
    "MIGRATION_DATABASE_URL": ({"MIGRATION_DATABASE_URL": "postgresql+psycopg://owner@x/y"}, "MIGRATION_DATABASE_URL must not be configured"),
    "no PUBLIC_ORIGIN": ({"PUBLIC_ORIGIN": ""}, "PUBLIC_ORIGIN is required"),
    "http PUBLIC_ORIGIN": ({"PUBLIC_ORIGIN": "http://app.example.test"}, "https"),
    "localhost PUBLIC_ORIGIN": ({"PUBLIC_ORIGIN": "https://localhost:3000"}, "localhost"),
    "wildcard CORS": ({"CORS_ORIGINS": '["*"]'}, "wildcard"),
    "localhost CORS": ({"CORS_ORIGINS": '["http://localhost:3000"]'}, "localhost"),
    "no BFF secret": ({"BFF_INTERNAL_SECRET": ""}, "BFF_INTERNAL_SECRET"),
    "short BFF secret": ({"BFF_INTERNAL_SECRET": "short"}, "at least 32"),
    "no APP_ENV": ({"APP_ENV": ""}, "app_env"),
}


@pytest.mark.parametrize("name", list(UNSAFE_BACKEND), ids=list(UNSAFE_BACKEND))
def test_the_backend_container_refuses_an_unsafe_production_configuration(backend_image, name):
    changes, rule = UNSAFE_BACKEND[name]
    code, output = exits(backend_image, {**BACKEND_ENV, "DATABASE_URL": DB_URL, **changes})
    assert code not in (None, 0), "the backend kept running with an unsafe production configuration"
    assert rule in output, output[-600:]
    for secret in (SENTINEL_PASSWORD, "appuser", BFF_SECRET, SECURITY_KEY):
        assert secret not in output, secret  # the rule is printed, never a configured value


def test_the_complete_production_configuration_starts_and_serves(backend_image):
    with container(backend_image, {**BACKEND_ENV, "DATABASE_URL": DB_URL}) as identifier:
        for _ in range(60):
            probe = docker("exec", identifier, "python", "-c", "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health').status)", check=False)
            if probe.stdout.strip() == "200":
                break
            time.sleep(0.5)
        else:
            raise AssertionError("it never answered")
        logs = docker("logs", identifier).stdout + docker("logs", identifier).stderr
        assert "Uvicorn running" in logs and SENTINEL_PASSWORD not in logs and BFF_SECRET not in logs


def test_the_backend_container_has_no_cors_middleware_and_demands_the_secret(backend_image):
    script = (
        "import json, urllib.request, urllib.error\n"
        "def call(path, headers):\n"
        "    try:\n        r = urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000' + path, headers=headers), timeout=5); return r.status, dict(r.headers)\n"
        "    except urllib.error.HTTPError as e:\n        return e.code, dict(e.headers)\n"
        "print(json.dumps([call('/api/me/user', {}), call('/api/me/user', {'x-bff-secret': '" + BFF_SECRET + "'}), call('/api/me/user', {'Origin': 'http://localhost:3000', 'x-bff-secret': '" + BFF_SECRET + "'})]))\n"
    )
    with container(backend_image, {**BACKEND_ENV, "DATABASE_URL": DB_URL}) as identifier:
        for _ in range(60):
            if docker("exec", identifier, "python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')", check=False).returncode == 0:
                break
            time.sleep(0.5)
        refused, accepted, with_origin = json.loads(docker("exec", identifier, "python", "-c", script).stdout)
        assert refused[0] == 403 and refused[1]["x-internal-auth"] == "rejected"
        assert accepted[0] == 401  # the application answered
        assert not any(name.lower().startswith("access-control") for name in with_origin[1])  # no CORS: the middleware is not installed
        assert {"x-content-type-options", "x-frame-options", "referrer-policy", "x-request-id"} <= {name.lower() for name in accepted[1]}
        assert "server" not in {name.lower() for name in accepted[1]}  # --no-server-header


# --- the production frontend --------------------------------------------------------------------------------------------------------------------------


def test_the_production_frontend_serves_hsts_headers_and_health_and_hides_the_secret(frontend_images):
    with serve(frontend_images["bare"], PRODUCTION_RUNTIME) as identifier:
        port = host_port(identifier, 3000)
        wait_http(port, "/api/health")
        for path in ("/login", "/api/health", "/api/auth/pre", "/no-such-page"):
            status, headers, body = page(port, path)
            assert headers["strict-transport-security"] == "max-age=31536000", path
            assert not re.search(r"includeSubDomains|preload", headers["strict-transport-security"], re.IGNORECASE)
            assert headers["x-content-type-options"] == "nosniff" and headers["x-frame-options"] == "DENY" and "permissions-policy" in headers
            assert BFF_SECRET not in body and BFF_SECRET not in json.dumps(headers)
        assert page(port, "/login")[1]["referrer-policy"] == "no-referrer" and page(port, "/api/health")[1]["referrer-policy"] == "strict-origin-when-cross-origin"
        status, _, body = page(port, "/api/health")
        assert (status, json.loads(body)) == (200, {"status": "ok"})
        # the backend is unreachable here: readiness says so coarsely, and the secret and address stay private
        status, headers, body = page(port, "/api/ready")
        assert (status, json.loads(body)) == (503, {"status": "unready"})
        assert "backend" not in body.lower() and BFF_SECRET not in body
        # nothing in the downloadable static files carries the secret (it exists only in the server's environment)
        found = docker("exec", identifier, "sh", "-c", f"grep -rIl -e {BFF_SECRET} /app/.next/static /app/public 2>/dev/null | head -3").stdout.strip()
        assert found == ""
        logs = docker("logs", identifier).stdout + docker("logs", identifier).stderr
        assert BFF_SECRET not in logs and '"event":"request"' in logs
        assert inspect(identifier)["State"]["Running"]


def test_the_frontend_container_health_check_is_liveness_and_stays_healthy_while_the_backend_is_down(frontend_images):
    with serve(frontend_images["bare"], PRODUCTION_RUNTIME) as identifier:
        config = inspect(identifier)["Config"]["Healthcheck"]["Test"]
        assert "/api/health" in " ".join(config) and "/api/ready" not in " ".join(config)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if inspect(identifier)["State"]["Health"]["Status"] == "healthy":
                break
            time.sleep(2)
        assert inspect(identifier)["State"]["Health"]["Status"] == "healthy"  # with an unreachable backend: liveness only


PRODUCTION_FRONTEND_REFUSALS = {
    "no APP_ENV": ({"APP_ENV": ""}, "APP_ENV must be set"),
    "BACKEND_URL missing": ({"BACKEND_URL": ""}, "BACKEND_URL is required"),
    "BACKEND_URL localhost": ({"BACKEND_URL": "http://localhost:8000"}, "BACKEND_URL must be a private"),
    "BACKEND_URL public host": ({"BACKEND_URL": "https://sentinel-public-host.example.com"}, "BACKEND_URL must be a private"),
    "no BFF secret": ({"BFF_INTERNAL_SECRET": ""}, "BFF_INTERNAL_SECRET is required"),
    "short BFF secret": ({"BFF_INTERNAL_SECRET": "short"}, "at least 32"),
    "bad TRUSTED_PROXY_HOPS": ({"TRUSTED_PROXY_HOPS": "two"}, "TRUSTED_PROXY_HOPS"),
}


@pytest.mark.parametrize("name", list(PRODUCTION_FRONTEND_REFUSALS), ids=list(PRODUCTION_FRONTEND_REFUSALS))
def test_the_production_frontend_refuses_an_incomplete_or_unsafe_configuration_naming_only_the_rule(frontend_images, name):
    changes, rule = PRODUCTION_FRONTEND_REFUSALS[name]
    code, output = exits(frontend_images["bare"], {**PRODUCTION_RUNTIME, **changes})
    assert code == 1, (code, output[-500:])
    assert rule in output
    for value in ("sentinel-public-host", BFF_SECRET, "short"):
        if value == "short" and name != "short BFF secret":
            continue
        if value == "short":
            assert "BFF_INTERNAL_SECRET=short" not in output  # the rule is printed, never the value
        else:
            assert value not in output


def test_development_keeps_its_conveniences_no_secret_no_backend_url_plain_http(frontend_images):
    env = {"AUTH_MODE": "session", "APP_ENV": "development", "PUBLIC_ORIGIN": "http://localhost:3000"}
    with serve(frontend_images["bare"], env) as identifier:
        port = host_port(identifier, 3000)
        wait_http(port, "/api/health")
        status, headers, _ = page(port, "/login")
        assert status == 200 and "strict-transport-security" not in headers  # development never sends HSTS
        assert page(port, "/api/ready")[0] == 503  # the localhost default backend is not there; still coarse
