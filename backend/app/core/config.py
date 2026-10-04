import secrets
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The .env file lives in the repository root, shared with Docker Compose.
ROOT_DIR = Path(__file__).resolve().parents[3]

AuthMode = Literal["dev", "session", "disabled"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    database_url: str
    cors_origins: list[str] = ["http://localhost:3000"]

    # Defaults fail closed: unless .env opts in, there is no dev identity.
    app_env: Literal["development", "production"] = "production"
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
    # safe when FastAPI is reachable from the BFF alone; behind the BFF it must be enabled (otherwise every
    # user shares the BFF's address).
    trust_client_ip_header: bool = False
    client_ip_header: str = "x-client-ip"

    # --- operator links and retention -----------------------------------------------------------------------------------------------
    setup_token_ttl_hours: int = 24
    public_origin: str = "http://localhost:3000"  # used to print setup links
    security_event_retention_days: int = 30
    auth_record_retention_days: int = 30  # expired or revoked sessions and used or expired setup tokens

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
            ("security_event_retention_days", 1),  # the database trigger also refuses deleting events younger than a day
            ("auth_record_retention_days", 1),
        ):
            if getattr(self, name) < minimum:
                raise ValueError(f"{name.upper()} must be at least {minimum}")
        if not 12 <= self.password_min_length <= self.password_max_length <= 1024:
            raise ValueError("password length limits must satisfy 12 <= min <= max <= 1024")
        return self


settings = Settings()
