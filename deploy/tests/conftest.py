"""Container and topology tests for the production images (slice D1).

These tests need a running Docker engine and take a few minutes (they build real images). They are NOT part of the
ordinary backend suite. Run them with the backend's environment:

    cd backend && uv run pytest ../deploy/tests -q

They use only throw-away containers, randomly named images and a disposable PostgreSQL on tmpfs: no development or
production data is read or written, and every container they start is removed afterwards.
"""

import json
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"
COMPOSE_FILE = ROOT / "deploy" / "compose.rehearsal.yml"

RUN = secrets.token_hex(3)
BACKEND_IMAGE = f"bp-d1-test-backend-{RUN}"
FRONTEND_IMAGES = {
    # name -> build arguments that set the BUILD environment (the Dockerfile's TEST_BUILD_* arguments; real builds pass none)
    "bare": {},
    "session": {"TEST_BUILD_MODE": "session", "TEST_BUILD_ENVIRONMENT": "production", "TEST_BUILD_ORIGIN": "https://build.example.test"},
    "dev": {"TEST_BUILD_MODE": "dev", "TEST_BUILD_ENVIRONMENT": "development", "TEST_BUILD_BACKEND": "http://build-time-backend.invalid:9999"},
}


def docker(*args: str, check: bool = True, timeout: int = 900, input: str | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, input=input)
    if check and result.returncode != 0:
        raise AssertionError(f"docker {' '.join(args)} failed ({result.returncode}):\n{result.stdout[-2000:]}\n{result.stderr[-3000:]}")
    return result


def build(context: Path, tag: str, build_args: dict[str, str] | None = None, extra: list[str] | None = None) -> None:
    args = ["build", "-t", tag, *(extra or [])]
    for key, value in (build_args or {}).items():
        args += ["--build-arg", f"{key}={value}"]
    docker(*args, str(context))


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # report redirects instead of following them
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def get(url: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], str]:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with OPENER.open(request, timeout=10) as response:
            return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, {k.lower(): v for k, v in error.headers.items()}, error.read().decode("utf-8", "replace")


@contextmanager
def container(image: str, env: dict[str, str] | None = None, publish: int | None = None, args: list[str] | None = None, remove_on_exit: bool = True) -> Iterator[str]:
    """A detached throw-away container (removed afterwards). Yields its id."""
    command = ["run", "-d", *(["--rm"] if remove_on_exit else [])]
    for key, value in (env or {}).items():
        command += ["-e", f"{key}={value}"]
    if publish is not None:
        command += ["-p", f"127.0.0.1::{publish}"]
    command += args or []
    command.append(image)
    identifier = docker(*command).stdout.strip()
    try:
        yield identifier
    finally:
        docker("rm", "-f", identifier, check=False)


def host_port(identifier: str, container_port: int) -> int:
    for _ in range(20):
        out = docker("port", identifier, str(container_port), check=False).stdout.strip()
        if out:
            return int(out.splitlines()[0].rsplit(":", 1)[1])
        time.sleep(0.25)
    raise AssertionError("the container published no port")


def wait_http(port: int, path: str = "/login", timeout: float = 40) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            get(f"http://127.0.0.1:{port}{path}")
            return
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.4)
    raise AssertionError(f"nothing answered on port {port}")


def inspect(identifier: str) -> dict:
    return json.loads(docker("inspect", identifier).stdout)[0]


@pytest.fixture(scope="session")
def backend_image() -> Iterator[str]:
    build(BACKEND_DIR, BACKEND_IMAGE)
    yield BACKEND_IMAGE
    docker("rmi", "-f", BACKEND_IMAGE, check=False)


@pytest.fixture(scope="session")
def frontend_images() -> Iterator[dict[str, str]]:
    tags = {}
    for name, build_args in FRONTEND_IMAGES.items():
        tags[name] = f"bp-d1-test-frontend-{name}-{RUN}"
        build(FRONTEND_DIR, tags[name], build_args)
    yield tags
    for tag in tags.values():
        docker("rmi", "-f", tag, check=False)


BFF_SECRET = "5c1f9a3e7b2d40869e1c7a35b8d20f64a1c7e903d5b6f2a8"
SECURITY_KEY = "9e4b7a1d3c6f20851b7d9a4e6c3f08295d1a7b4e"

# A COMPLETE production configuration (D2): every rule the backend enforces at startup is satisfied.
BACKEND_ENV = {
    "APP_ENV": "production",
    "AUTH_MODE": "session",
    "SECURITY_KEY": SECURITY_KEY,
    "BFF_INTERNAL_SECRET": BFF_SECRET,
    "PUBLIC_ORIGIN": "https://app.example.test",
    "DATABASE_URL": "postgresql+psycopg://nobody:nothing@database.invalid:5432/none",  # unreachable on purpose
    "CORS_ORIGINS": "[]",
}
