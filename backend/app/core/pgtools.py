"""What the backup, restore and verification tools share: reading an explicit database URL, running the PostgreSQL client
programs without a credential on their command line, and scrubbing whatever they print.

Rules (docs/backup-restore.md): a tool takes its database from ONE named environment variable and never falls back to another
(`DATABASE_URL` is for the web process, not for operators); the password reaches `pg_dump`/`pg_restore` only through the
`PGPASSWORD` environment variable, never through argv; nothing a client prints is shown before every credential-looking string
has been removed.
"""

import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

DUMP_ENV, RESTORE_ENV = "BP_PG_DUMP", "BP_PG_RESTORE"  # override the client program (tests; an unusual install)
_PASSTHROUGH_QUERY = {"sslmode": "PGSSLMODE", "sslrootcert": "PGSSLROOTCERT", "sslcert": "PGSSLCERT", "sslkey": "PGSSLKEY", "connect_timeout": "PGCONNECT_TIMEOUT"}


class ToolRefused(Exception):
    """A configuration or safety refusal (exit code 2): nothing was done."""


@dataclass(frozen=True)
class Target:
    host: str
    port: int
    user: str
    password: str | None
    database: str
    options: tuple[tuple[str, str], ...] = ()

    def identity(self) -> tuple[str, int, str]:
        """Where the database is, without who connects: two URLs with the same identity are the same database."""
        return (self.host.lower(), self.port, self.database)

    def conn_kwargs(self) -> dict[str, object]:
        kwargs: dict[str, object] = {"host": self.host, "port": self.port, "user": self.user, "dbname": self.database}
        if self.password is not None:
            kwargs["password"] = self.password
        kwargs.update({key: value for key, value in self.options if key in _PASSTHROUGH_QUERY})
        return kwargs

    def client_env(self) -> dict[str, str]:
        """The PG* environment for a client program: host, port, role and database, and the password, but never an argument."""
        env = {"PGHOST": self.host, "PGPORT": str(self.port), "PGUSER": self.user, "PGDATABASE": self.database}
        if self.password is not None:
            env["PGPASSWORD"] = self.password
        for key, value in self.options:
            if key in _PASSTHROUGH_QUERY:
                env[_PASSTHROUGH_QUERY[key]] = value
        return env

    def secrets(self) -> list[str]:
        return [value for value in (self.password,) if value]


def target_from_env(variable: str, environ: dict[str, str] | None = None) -> Target:
    """The database named by ONE explicit variable. Missing or malformed is a refusal; there is no other source."""
    raw = (environ if environ is not None else os.environ).get(variable, "")
    if not raw.strip():
        raise ToolRefused(f"{variable} is not set (this tool never falls back to DATABASE_URL or any other database)")
    return parse_url(raw, variable)


def parse_url(raw: str, name: str = "the database URL") -> Target:
    try:
        url = make_url(raw.strip())
    except ArgumentError:
        raise ToolRefused(f"{name} is not a valid database URL") from None  # (the library's message would quote the URL)
    if not url.drivername.startswith("postgresql"):
        raise ToolRefused(f"{name} is not a PostgreSQL URL")
    if not url.host:
        raise ToolRefused(f"{name} has no host: the host must be explicit")
    if not url.username or not url.database:
        raise ToolRefused(f"{name} must name a user and a database")
    options = tuple((key, str(value)) for key, value in sorted(url.query.items()))
    return Target(url.host, url.port or 5432, url.username, url.password, url.database, options)


_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)  # the whole URL: credentials, but also the host and database name
_CONNECTION_TARGET = re.compile(r'connection to server (?:at|on socket) "[^"]*"( \([^)]*\))?(, port \d+)?', re.IGNORECASE)
_PASSWORD_WORD = re.compile(r"(?i)(password(?: authentication failed)?[^\n]*?[:=]\s*)\S+")


def scrub(text: str, secrets: list[str] | None = None, limit: int = 600) -> str:
    """`text` with every known secret, URL credential and server address removed and bounded in length: for a log line or a
    message shown to an operator. The client programs can quote a connection string or a host in their errors."""
    for secret in secrets or []:
        if secret:
            text = text.replace(secret, "[redacted]")
    text = _URL.sub("[redacted-url]", text)
    text = _CONNECTION_TARGET.sub("connection to server [redacted]", text)
    text = _PASSWORD_WORD.sub(r"\1[redacted]", text)
    return text.strip()[:limit]


def client_command(env_name: str, default: str) -> list[str]:
    """The program (and fixed leading arguments) behind pg_dump / pg_restore: `BP_PG_DUMP` / `BP_PG_RESTORE` as a JSON array
    or a plain path, else the program on PATH."""
    configured = os.environ.get(env_name, "").strip()
    if not configured:
        return [default]
    if configured.startswith("["):
        parsed = json.loads(configured)
        if not isinstance(parsed, list) or not parsed or not all(isinstance(item, str) for item in parsed):
            raise ToolRefused(f"{env_name} must be a JSON array of strings or a path")
        return parsed
    return [configured]


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str


def run_client(command: list[str], arguments: list[str], target: Target | None, *, timeout: float | None = None) -> Completed:
    """Run a client program with the target's credentials in its environment (never its arguments); output comes back raw,
    so callers must `scrub` before showing it."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PG")}  # no ambient PG* fallback
    if target is not None:
        environment.update(target.client_env())
    try:
        result = subprocess.run([*command, *arguments], env=environment, capture_output=True, text=True, timeout=timeout, check=False, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise ToolRefused(f"the PostgreSQL client program {Path(command[0]).name!r} was not found (install the PostgreSQL 17 client or set {DUMP_ENV}/{RESTORE_ENV})") from None
    except subprocess.TimeoutExpired:
        return Completed(124, "", "the client program timed out")
    return Completed(result.returncode, result.stdout, result.stderr)


def client_major_version(command: list[str]) -> int | None:
    result = run_client(command, ["--version"], None, timeout=30)
    found = re.search(r"(\d+)(?:\.\d+)*", result.stdout or "")
    return int(found.group(1)) if result.returncode == 0 and found else None
