import ipaddress
import secrets
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

# The .env file lives in the repository root, shared with Docker Compose.
ROOT_DIR = Path(__file__).resolve().parents[3]

AuthMode = Literal["dev", "session", "disabled"]

# Secrets must be long enough to be unguessable and not trivially repetitive ("aaaa...", "abababab...").
SECRET_MIN_LENGTH = 32
SECRET_MIN_DISTINCT_CHARACTERS = 8
LOCAL_HOSTS = {"localhost", "ip6-localhost", "ip6-loopback", "0.0.0.0", "::", "::1"}


def is_local_host(host: str) -> bool:
    """True for localhost in every spelling a configuration could use (names, 127.0.0.0/8, ::1, the unspecified address)."""
    host = host.strip("[]").lower().rstrip(".")
    if host in LOCAL_HOSTS or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def public_origin_problem(raw: str | None) -> str | None:
    """Why `raw` cannot be the public origin of a production deployment, or None: an https origin (scheme, host and
    optional port only) that is not localhost."""
    if raw is None or raw.strip() == "":
        return "PUBLIC_ORIGIN is required in production"
    try:
        url = urlsplit(raw.strip())
        host = url.hostname
        url.port  # noqa: B018  (raises ValueError for a malformed port)
    except ValueError:
        return "PUBLIC_ORIGIN is not a valid URL"
    if url.scheme != "https":
        return "PUBLIC_ORIGIN must be an https origin in production"
    if not host or url.username is not None or url.password is not None or url.path not in ("", "/") or url.query or url.fragment:
        return "PUBLIC_ORIGIN must be an origin only (https, host and optional port)"
    if is_local_host(host):
        return "PUBLIC_ORIGIN must not be localhost in production"
    return None


def secret_problem(name: str, value: str | None) -> str | None:
    if value is None or len(value) < SECRET_MIN_LENGTH:
        return f"{name} must be at least {SECRET_MIN_LENGTH} characters"
    if len(set(value)) < SECRET_MIN_DISTINCT_CHARACTERS:
        return f"{name} is too repetitive to be a secret"
    return None


class Settings(BaseSettings):
    # hide_input_in_errors: a failed validation prints the rule that failed, never the input (it would include the other
    # configured values: database URLs with passwords, secrets).
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore", hide_input_in_errors=True)

    database_url: str
    # The BROWSER never calls FastAPI (the BFF does), so production has no CORS origin at all and the middleware is not
    # installed. Development keeps the Next.js dev origin as a convenience.
    cors_origins: list[str] | None = None

    # No default: a process that was not told which environment it is in does not start.
    app_env: Literal["development", "production"]
    # "dev": the development identity header (APP_ENV=development only).
    # "session": real authentication with server-side sessions (the only mode allowed in production).
    # "disabled": nobody is ever authenticated.
    auth_mode: AuthMode = "disabled"
    dev_user_email: str | None = None  # default dev identity when no X-Dev-User-Email header

    # --- sessions (AUTH_MODE=session) -------------------------------------------------------------------------------------------
    session_idle_hours: int = 12
    session_absolute_days: int = 7
    session_max_per_user: int = 20
    # last_used_at is refreshed at most this often, so reading does not write on every request. The idle
    # limit can therefore be up to this many seconds stricter than "idle hours since the last request".
    session_touch_seconds: int = 300

    # --- passwords: Argon2id --------------------------------------------------------------------------------------------------------
    # Starting point (about 100 to 250 ms on small server hardware): tune on the deployment hardware.
    argon2_memory_kib: int = 65536
    argon2_time_cost: int = 3
    argon2_parallelism: int = 1
    password_min_length: int = 12
    password_max_length: int = 128
    # Bounded admission to password hashing: at most `argon2_max_concurrent` hashes run at once, at most
    # `argon2_max_waiting` more requests may wait (at most `argon2_wait_seconds`), and any further request is
    # refused at once (503) instead of queueing without limit.
    argon2_max_concurrent: int = 4
    argon2_max_waiting: int = 8
    argon2_wait_seconds: float = 2.0

    # --- login abuse protection (see docs/architecture.md, "Login abuse protection") -----------------------------------------------
    throttle_window_seconds: int = 900
    throttle_source_max_failures: int = 30  # failed logins per source in the window
    throttle_pair_max_failures: int = 5  # per (source, login identifier)
    throttle_identifier_max_failures: int = 50  # per login identifier across all sources (unknown sources only)
    throttle_global_max_failure_events: int = 5000  # safety valve: failure rows written per window, in total
    setup_source_max_failures: int = 10  # failed setup-link redemptions per source in the window
    password_change_max_failures: int = 5  # wrong current passwords per user in the window
    # HMAC key for the login identifier recorded in security events (so the table never holds an email, and
    # attacker-chosen identifiers have a fixed size). Required with AUTH_MODE=session outside development.
    security_key: SecretStr | None = None
    # Take the client address from `client_ip_header` (set by the BFF) instead of the connection's peer. ONLY
    # safe when FastAPI is reachable from the BFF alone, and it is honoured only on a request that already passed the
    # BFF internal-secret check (so it requires BFF_INTERNAL_SECRET). Stays false until the deployment's proxy chain is
    # verified (D5): until then every user shares the BFF's address.
    trust_client_ip_header: bool = False
    client_ip_header: str = "x-client-ip"

    # --- operator links and retention -----------------------------------------------------------------------------------------------
    setup_token_ttl_hours: int = 24
    invitation_ttl_days: int = 7  # how long an organization invitation can be accepted
    # The canonical browser origin, used to print setup links. Required (https, not localhost) in production;
    # development defaults to the Next.js dev origin.
    public_origin: str | None = None
    security_event_retention_days: int = 30
    auth_record_retention_days: int = 30  # expired or revoked sessions and used or expired setup tokens

    # --- production trust boundary ---------------------------------------------------------------------------------------------------
    # A shared secret the BFF sends on EVERY request (defence in depth: network isolation stays mandatory). Required in
    # production. Elsewhere it is optional, and enforcement follows the setting: when it is set it is enforced, so a
    # development or test run can exercise the whole chain, and when it is not set nothing is checked (development only).
    bff_internal_secret: SecretStr | None = None
    bff_internal_header: str = "x-bff-secret"
    # Only to be REFUSED in production: a web process must never hold DDL credentials, a test database, or a dev identity.
    migration_database_url: str | None = None
    test_database_url: str | None = None

    @model_validator(mode="after")
    def check_auth_configuration(self) -> "Settings":
        if self.auth_mode == "dev" and self.app_env != "development":
            raise ValueError("AUTH_MODE=dev is only allowed when APP_ENV=development")
        if self.app_env == "production" and self.auth_mode != "session":
            raise ValueError("APP_ENV=production requires AUTH_MODE=session (there is no other production mode)")
        if self.auth_mode == "session":
            if self.security_key is None or len(self.security_key.get_secret_value()) < 32:
                if self.app_env == "production":
                    raise ValueError("AUTH_MODE=session requires SECURITY_KEY (at least 32 characters)")
                # Development convenience: a key for this process only. Counters restart with the process.
                self.security_key = SecretStr(secrets.token_urlsafe(32))
        for name, minimum in (
            ("session_idle_hours", 1),
            ("session_absolute_days", 1),
            ("session_max_per_user", 1),
            ("session_touch_seconds", 1),
            ("argon2_memory_kib", 8),
            ("argon2_time_cost", 1),
            ("argon2_parallelism", 1),
            ("argon2_max_concurrent", 1),
            ("argon2_max_waiting", 0),
            ("throttle_window_seconds", 1),
            ("throttle_source_max_failures", 1),
            ("throttle_pair_max_failures", 1),
            ("throttle_identifier_max_failures", 1),
            ("throttle_global_max_failure_events", 1),
            ("setup_source_max_failures", 1),
            ("password_change_max_failures", 1),
            ("setup_token_ttl_hours", 1),
            ("invitation_ttl_days", 1),
            ("security_event_retention_days", 1),  # the database trigger also refuses deleting events younger than a day
            ("auth_record_retention_days", 1),
        ):
            if getattr(self, name) < minimum:
                raise ValueError(f"{name.upper()} must be at least {minimum}")
        if not 12 <= self.password_min_length <= self.password_max_length <= 1024:
            raise ValueError("password length limits must satisfy 12 <= min <= max <= 1024")
        return self


    @model_validator(mode="after")
    def check_production_configuration(self) -> "Settings":
        try:
            make_url(self.database_url)
        except ArgumentError:
            # (SQLAlchemy's own message quotes the whole string, password included: say only what is wrong.)
            raise ValueError("DATABASE_URL is not a valid database URL") from None
        if self.public_origin is None and self.app_env != "production":
            self.public_origin = "http://localhost:3000"
        if self.cors_origins is None:
            self.cors_origins = [] if self.app_env == "production" else ["http://localhost:3000"]
        secret = self.bff_internal_secret.get_secret_value() if self.bff_internal_secret else None
        if secret is not None and (problem := secret_problem("BFF_INTERNAL_SECRET", secret)):
            raise ValueError(problem)
        if self.trust_client_ip_header and secret is None:
            # The client-address header is only believable from an authenticated BFF.
            raise ValueError("TRUST_CLIENT_IP_HEADER=true requires BFF_INTERNAL_SECRET")
        if self.app_env != "production":
            return self

        if (problem := public_origin_problem(self.public_origin)) is not None:
            raise ValueError(problem)
        if secret is None:
            raise ValueError("BFF_INTERNAL_SECRET is required in production")
        if self.security_key is not None and secret == self.security_key.get_secret_value():
            raise ValueError("BFF_INTERNAL_SECRET must differ from SECURITY_KEY")
        for origin in self.cors_origins:
            if origin.strip() == "*":
                raise ValueError("a wildcard CORS origin is not allowed in production")
            host = urlsplit(origin).hostname or origin
            if is_local_host(host):
                raise ValueError("a localhost CORS origin is not allowed in production")
        if self.cors_origins:
            raise ValueError("CORS_ORIGINS must be [] in production (the browser never calls FastAPI)")
        if self.dev_user_email is not None:
            raise ValueError("DEV_USER_EMAIL must not be configured in production")
        if self.test_database_url is not None:
            raise ValueError("TEST_DATABASE_URL must not be configured in production")
        if self.migration_database_url is not None:
            raise ValueError("MIGRATION_DATABASE_URL must not be configured on the web process (only the migration job uses it)")
        return self


settings = Settings()
