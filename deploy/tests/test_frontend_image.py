"""The frontend production image: runtime user, standalone server, behaviour driven by RUNTIME configuration only."""

import re
import time

import pytest

from conftest import BFF_SECRET, ROOT, container, docker, get, host_port, inspect, wait_http, build, FRONTEND_DIR

SESSION_RUNTIME = {"AUTH_MODE": "session", "APP_ENV": "development", "PUBLIC_ORIGIN": "http://localhost:3000", "BACKEND_URL": "http://backend.invalid:8000"}
DEV_RUNTIME = {"AUTH_MODE": "dev", "APP_ENV": "development", "BACKEND_URL": "http://backend.invalid:8000"}
# A complete production configuration (D2): https origin, a private (single-label) backend name, the internal secret.
PRODUCTION_RUNTIME = {"AUTH_MODE": "session", "APP_ENV": "production", "PUBLIC_ORIGIN": "https://app.example.test", "BACKEND_URL": "http://backend:8000", "BFF_INTERNAL_SECRET": BFF_SECRET}


def serve(image, env):
    return container(image, env, publish=3000)


def page(port, path, headers=None):
    return get(f"http://127.0.0.1:{port}{path}", headers)


def sh(image, command):
    return docker("run", "--rm", "--entrypoint", "sh", image, "-c", command, check=False).stdout


def test_it_runs_as_a_non_root_user_with_the_standalone_server(frontend_images):
    image = frontend_images["bare"]
    config = inspect(image)["Config"]
    assert config["User"] == "node" and config["Cmd"] == ["node", "server.js"] and config["ExposedPorts"] == {"3000/tcp": {}}
    assert docker("run", "--rm", "--entrypoint", "id", image, "-u").stdout.strip() == "1000"
    with serve(image, SESSION_RUNTIME) as identifier:
        port = host_port(identifier, 3000)
        wait_http(port)
        assert docker("exec", identifier, "id", "-u").stdout.strip() == "1000"
        status, headers, body = page(port, "/login")
        assert status == 200 and "Sign in" in body
        assert "x-powered-by" not in headers  # the header is not sent
        assert docker("exec", identifier, "cat", "/proc/1/cmdline").stdout.startswith("next-server")  # PID 1 is the standalone Next server (it renames its process)


# The same behaviour must come out of EVERY image, whatever environment it was built in: the matrix is images x runtimes.
@pytest.mark.parametrize("built_in", ["bare", "session", "dev"])
def test_behaviour_follows_the_runtime_configuration_not_the_build_environment(frontend_images, built_in):
    image = frontend_images[built_in]
    with serve(image, SESSION_RUNTIME) as identifier:
        port = host_port(identifier, 3000)
        wait_http(port)
        status, _, body = page(port, "/setup")
        assert status == 200 and "Set your password" in body  # first-operator setup works in session mode
        assert page(port, "/login")[0] == 200
        assert page(port, "/invite")[0] == 200 and "Join an organization" in page(port, "/invite")[2]
        assert page(port, "/dev-login")[0] == 404  # no development sign-in in session mode
        status, headers, _ = page(port, "/")
        assert status in (302, 307) and headers["location"].endswith("/login")
    with serve(image, DEV_RUNTIME) as identifier:
        port = host_port(identifier, 3000)
        wait_http(port, "/dev-login")
        status, _, body = page(port, "/dev-login")
        assert status == 200 and "Development sign-in" in body  # follows the RUNTIME mode even if built as session/production
        assert page(port, "/setup")[0] == 404 and page(port, "/login")[0] == 404 and page(port, "/invite")[0] == 404
        status, headers, _ = page(port, "/")
        assert status in (302, 307) and headers["location"].endswith("/dev-login")
    with serve(image, PRODUCTION_RUNTIME) as identifier:
        port = host_port(identifier, 3000)
        wait_http(port)
        assert page(port, "/setup")[0] == 200 and page(port, "/dev-login")[0] == 404
        status, headers, _ = page(port, "/api/auth/pre")
        assert status == 200 and headers["set-cookie"].startswith("__Host-bp_pre=") and "Secure" in headers["set-cookie"]


def test_the_build_output_has_no_prerendered_page_that_depends_on_configuration(frontend_images):
    for name, image in frontend_images.items():
        manifest = sh(image, "cat /app/.next/prerender-manifest.json")
        import json

        routes = sorted(json.loads(manifest)["routes"])
        assert routes == ["/_global-error", "/_not-found", "/favicon.ico"], (name, routes)
        for route in ("setup", "dev-login", "login", "invite"):
            assert sh(image, f"test -e /app/.next/server/app/{route}.html && echo baked").strip() == "", (name, route)  # control below
    assert sh(frontend_images["bare"], "test -e /app/.next/server/app/_not-found.html && echo baked").strip() == "baked"


@pytest.mark.parametrize(
    "env",
    [
        {"AUTH_MODE": "dev", "APP_ENV": "production", "BACKEND_URL": "http://backend.invalid:8000"},
        {"AUTH_MODE": "session", "APP_ENV": "production", "PUBLIC_ORIGIN": "http://insecure.example.test", "BACKEND_URL": "http://backend.invalid:8000"},
        {"AUTH_MODE": "session", "APP_ENV": "production", "BACKEND_URL": "http://backend.invalid:8000"},
        {"APP_ENV": "production", "BACKEND_URL": "http://backend.invalid:8000"},
    ],
    ids=["dev-mode-in-production", "http-origin-in-production", "no-origin-in-production", "no-mode-in-production"],
)
def test_production_refuses_to_start_with_an_unsafe_or_missing_configuration(frontend_images, env):
    with container(frontend_images["bare"], env, publish=3000, remove_on_exit=False) as identifier:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and inspect(identifier)["State"]["Running"]:
            time.sleep(0.5)
        state = inspect(identifier)["State"]
        logs = docker("logs", identifier, check=False)
        assert not state["Running"], "the server kept running (answering 500s) with an unusable production configuration"
        assert state["ExitCode"] == 1
        assert "Authentication is misconfigured" in logs.stdout + logs.stderr


def test_the_browser_bundle_carries_no_backend_address_or_server_only_secret_name(frontend_images):
    image = frontend_images["bare"]
    forbidden = "-e BACKEND_URL -e localhost:8000 -e backend:8000 -e x-dev-user-email -e bp_session -e DATABASE_URL -e SECURITY_KEY"
    assert sh(image, f"cd /app/.next/static && grep -rIl {forbidden} . | head -5").strip() == ""
    # a non-vacuous control: the same scan does find a string the bundle really contains
    assert sh(image, "cd /app/.next/static && grep -rIl -e '/api/auth/login' . | head -1").strip() != ""
    with serve(image, {**SESSION_RUNTIME, "BACKEND_URL": "http://sentinel-backend-host.invalid:8000"}) as identifier:
        port = host_port(identifier, 3000)
        wait_http(port)
        for path in ("/login", "/setup", "/invite"):
            assert "sentinel-backend-host" not in page(port, path)[2], path  # the runtime value never reaches the browser either


def test_no_font_service_is_needed_to_build_and_none_is_referenced(frontend_images):
    image = frontend_images["bare"]
    assert sh(image, "cd /app/.next && grep -rIl -e fonts.googleapis -e fonts.gstatic . | head -3").strip() == ""
    assert sh(image, "cd /app/.next/static && ls media | grep -c -i -e woff2 -e ttf").strip() not in ("", "0")  # the bundled font is served locally


def test_the_image_builds_with_the_font_services_blackholed(tmp_path):
    from conftest import RUN

    tag = f"bp-d1-test-frontend-offline-fonts-{RUN}"
    try:
        build(FRONTEND_DIR, tag, extra=["--add-host", "fonts.googleapis.com:127.0.0.1", "--add-host", "fonts.gstatic.com:127.0.0.1", "--no-cache-filter", "build"])
    finally:
        docker("rmi", "-f", tag, check=False)


def test_the_final_image_has_only_what_the_standalone_server_needs(frontend_images):
    image = frontend_images["bare"]
    top = sorted(sh(image, "ls -A /app").split())
    assert top == [".next", "node_modules", "package.json", "public", "server.js"], top
    leftovers = sh(
        image,
        "cd /app; find . \\( -name '.env' -o -name '.env.*' -o -name '.git' -o -name 'e2e' -o -name 'test-results' -o -name 'playwright*' -o -name '*.test.*' -o -name '*.spec.*' -o -name 'vitest*' \\) -not -path './node_modules/*' | head; "
        "ls node_modules | grep -x -e typescript -e vitest -e eslint -e '@playwright' -e jsdom -e '@testing-library' | head; find /app -xdev -name '*.pem' -o -xdev -name '*.key' | head",
    )
    assert leftovers.strip() == "", leftovers
    assert sh(image, "find /app -xdev -type f -name '*.ts' -not -name '*.d.ts' -not -path '*/node_modules/*' | head -3").strip() == ""  # no TypeScript sources
    # node_modules were installed INSIDE the image (Linux), never taken from the host (Windows/macOS binaries would be wrong here)
    assert sh(image, "find /app/node_modules -iname '*win32*' -o -iname '*darwin*' | head -3").strip() == ""
    assert sh(image, "ls /app/node_modules/@img | grep -c linux").strip() not in ("", "0")  # control: the Linux-native packages ARE there
    assert sh(image, "grep -rIl -e TEST_DATABASE_URL -e POSTGRES_TEST -e business_platform_test /app --exclude-dir=node_modules 2>/dev/null | head -3").strip() == ""  # no test-database configuration
    environment = " ".join(inspect(image)["Config"]["Env"])
    assert not re.search(r"AUTH_MODE|PUBLIC_ORIGIN|BACKEND_URL|SECRET|PASSWORD|TOKEN", environment)  # nothing configured at build time
    history = docker("history", "--no-trunc", "--format", "{{.CreatedBy}}", image).stdout
    assert not re.search(r"\.env|TEST_BUILD_MODE=|PASSWORD", history.replace("ARG TEST_BUILD", ""))
