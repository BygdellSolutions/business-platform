"""A disposable rehearsal stack (compose project) for the D1/D2 container tests."""

import json
import os
import re
import secrets
import subprocess
import time

from conftest import BFF_SECRET, COMPOSE_FILE, RUN, docker, free_port, get

HEAD_PATTERN = re.compile(r"^revision[^=]*=\s*['\"]([0-9a-f]+)['\"]", re.M)


def repository_head() -> str:
    """The single Alembic head according to the repository's migration files."""
    from conftest import BACKEND_DIR

    revisions, parents = {}, set()
    for path in (BACKEND_DIR / "alembic" / "versions").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        revision = HEAD_PATTERN.search(text).group(1)
        down = re.search(r"^down_revision[^=]*=\s*(None|['\"]([0-9a-f]+)['\"])", text, re.M).group(2)
        revisions[revision] = down
        if down:
            parents.add(down)
    heads = set(revisions) - parents
    assert len(heads) == 1, heads
    return heads.pop()


class Stack:
    def __init__(self, label: str):
        self.project = f"bp-d2-{label}-{RUN}"
        self.port = free_port()
        self.passwords = {name: secrets.token_hex(12) for name in ("db", "owner", "app")}
        self.env = {
            **os.environ,
            "REHEARSAL_PROJECT": self.project,
            "REHEARSAL_PORT": str(self.port),
            "REHEARSAL_DB_PASSWORD": self.passwords["db"],
            "REHEARSAL_OWNER_PASSWORD": self.passwords["owner"],
            "REHEARSAL_APP_PASSWORD": self.passwords["app"],
            "REHEARSAL_SECURITY_KEY": secrets.token_hex(24),
            "REHEARSAL_BFF_SECRET": BFF_SECRET,
            "REHEARSAL_BACKEND_IMAGE": f"bp-d2-{label}-backend-{RUN}",
            "REHEARSAL_FRONTEND_IMAGE": f"bp-d2-{label}-frontend-{RUN}",
        }

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def compose(self, *args: str, check: bool = True, timeout: int = 900) -> subprocess.CompletedProcess:
        result = subprocess.run(["docker", "compose", "-f", str(COMPOSE_FILE), *args], env=self.env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        if check and result.returncode != 0:
            raise AssertionError(f"compose {' '.join(args)} failed ({result.returncode}):\n{result.stdout[-2500:]}\n{result.stderr[-3500:]}")
        return result

    def sql(self, query: str, user: str = "bp") -> str:
        """As the BOOTSTRAP superuser (the test harness only)."""
        return self.compose("exec", "-T", "postgres", "psql", "-U", user, "-d", "bp", "-At", "-c", query).stdout.strip()

    def container_id(self, service: str) -> str:
        return self.compose("ps", "-a", "-q", service).stdout.strip()

    def state(self, service: str) -> dict:
        identifier = self.container_id(service)
        assert identifier, f"no container for {service}"
        info = json.loads(docker("inspect", identifier).stdout)[0]
        return {**info["State"], "RestartCount": info["RestartCount"], "Id": identifier}

    def backend_probe(self, path: str, headers: dict[str, str] | None = None, timeout: int = 15) -> tuple[int, str]:
        """An HTTP request to the PRIVATE backend, made from inside its own container (nothing is published)."""
        script = (
            "import sys, json, urllib.request, urllib.error\n"
            "req = urllib.request.Request('http://127.0.0.1:8000' + sys.argv[1], headers=json.loads(sys.argv[2]))\n"
            "try:\n    r = urllib.request.urlopen(req, timeout=int(sys.argv[3])); print(r.status); print(r.read().decode())\n"
            "except urllib.error.HTTPError as e:\n    print(e.code); print(e.read().decode())\n"
        )
        out = self.compose("exec", "-T", "backend", "python", "-c", script, path, json.dumps(headers or {}), str(timeout), timeout=timeout + 30).stdout
        status, _, body = out.partition("\n")
        return int(status), body.strip()

    def ready(self) -> int:
        try:
            return get(f"{self.base}/api/ready")[0]
        except OSError:
            return 0

    def wait_ready(self, timeout: float = 150) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.ready() == 200:
                return
            time.sleep(1)
        raise AssertionError("the stack never became ready:\n" + self.compose("ps", "-a", check=False).stdout + self.compose("logs", "--tail", "40", check=False).stdout)

    def down(self) -> None:
        self.compose("down", "-v", "--remove-orphans", check=False)
        for image in (self.env["REHEARSAL_BACKEND_IMAGE"], self.env["REHEARSAL_FRONTEND_IMAGE"]):
            docker("rmi", "-f", image, check=False)
