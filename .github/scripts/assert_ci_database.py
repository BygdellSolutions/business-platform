"""Refuse to run a CI job against anything but the disposable test database it just started.

    python .github/scripts/assert_ci_database.py

The existing guards (`reset_test_db`, the Playwright `assertTestDatabase`) refuse a database that is not named `*_test` or
that shares a server with the development database. CI has no development database, so those comparisons cannot fire; this
script adds the CI-specific half, so a misconfigured job fails BEFORE any test touches a database:

  * TEST_DATABASE_URL must be set, name a `*_test` database and point at the runner's own loopback on the disposable server's
    port (nothing remote, nothing named, no default);
  * no DATABASE_URL, MIGRATION_DATABASE_URL, BOOTSTRAP_DATABASE_URL, PG* connection variable or any other DB URL may be set (they
    would be a fallback to somewhere that is not the disposable server);
  * there is no `.env` file in the checkout (a developer file never exists on a runner; if it does, something put it there).

Standard library only, so it runs before any dependency is installed.
"""

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
DISPOSABLE_PORT = 5433
FORBIDDEN_VARIABLES = (
    "DATABASE_URL",
    "MIGRATION_DATABASE_URL",
    "BOOTSTRAP_DATABASE_URL",
    "TEST_RUNTIME_ROLE_URL",
    "PGHOST",
    "PGHOSTADDR",
    "PGSERVICE",
    "PGSERVICEFILE",
    "PGPASSFILE",
    "PGDATABASE",
    "PGPORT",
    "PGUSER",
    "PGPASSWORD",
)


def problems(env: dict[str, str], repo_root: Path) -> list[str]:
    found: list[str] = []
    url = env.get("TEST_DATABASE_URL", "")
    if not url:
        found.append("TEST_DATABASE_URL is not set (CI never falls back to any other database)")
    else:
        try:
            parts = urlsplit(url)
            host, port, name = parts.hostname, parts.port, parts.path.lstrip("/")
        except ValueError:
            return found + ["TEST_DATABASE_URL is not a valid URL"]
        if not (parts.scheme == "postgresql" or parts.scheme.startswith("postgresql+")):
            found.append("TEST_DATABASE_URL is not a PostgreSQL URL")
        if host not in ALLOWED_HOSTS:
            found.append("TEST_DATABASE_URL must point at the runner's own loopback (127.0.0.1 or localhost), not a remote host")
        if port != DISPOSABLE_PORT:
            found.append(f"TEST_DATABASE_URL must use the disposable server's port {DISPOSABLE_PORT}")
        if not name.endswith("_test"):
            found.append("the database name must end in _test")
    for variable in FORBIDDEN_VARIABLES:
        if env.get(variable):
            found.append(f"{variable} must not be set in CI (it would be a fallback away from the disposable database)")
    for variable, value in env.items():
        if variable not in FORBIDDEN_VARIABLES and variable != "TEST_DATABASE_URL" and ("postgres://" in value or "postgresql://" in value or "postgresql+" in value):
            found.append(f"{variable} holds a database URL; CI jobs have exactly one, TEST_DATABASE_URL")
    if (repo_root / ".env").exists():
        found.append("a .env file exists in the checkout; a runner must have none")
    return found


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    found = problems(dict(os.environ), repo_root)
    if found:
        print("REFUSING to run: this is not a disposable CI database.", file=sys.stderr)
        for message in found:
            print(f"  - {message}", file=sys.stderr)  # (messages never include a URL or a value)
        return 1
    print("CI database guard: the only database is the disposable test server on loopback.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
