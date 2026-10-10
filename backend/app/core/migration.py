"""Everything the migration job and the readiness probe share: where the migrations are, which revision is THIS image's
single head, the migration credentials, the advisory lock and the grants the runtime role needs.

Deliberately independent of `app.core.config` / `app.core.db`: the migration job runs with its own credentials
(MIGRATION_DATABASE_URL) and must not need, or be handed, the web process's configuration or secrets.
"""

from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

BACKEND_DIR = Path(__file__).resolve().parents[2]
ROOT_DIR = BACKEND_DIR.parent
DEFAULT_SCRIPT_LOCATION = BACKEND_DIR / "alembic"

# One fixed key for the whole application: two migration processes (a restarted job, a second replica, an operator
# running the command by hand) can never run Alembic at the same time against the same database.
MIGRATION_LOCK_KEY = 0x62705F6D69677261  # "bp_migra"


class MigrationSettings(BaseSettings):
    """The migration job's own configuration. Never the web process's."""

    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore", hide_input_in_errors=True)

    app_env: str | None = None
    migration_database_url: str | None = None
    database_url: str | None = None  # development fallback only, see `url`
    # The application role that must be able to use what the migrations create (see `reconcile_runtime_grants`).
    runtime_db_role: str | None = None
    # TEST databases only: the confirmation phrase of `app.scripts.renumber_invoices` (unset everywhere else).
    renumber_invoices: str | None = None

    def url(self) -> str | None:
        """The credentials migrations run with. Production has no fallback: a missing MIGRATION_DATABASE_URL is an error,
        never a silent use of the runtime URL. Development may use DATABASE_URL (one local role does everything)."""
        if self.migration_database_url:
            return self.migration_database_url
        if self.app_env == "development" and self.database_url:
            return self.database_url
        return None


def migration_engine(url: str) -> Engine:
    """hide_parameters: a failing migration statement never echoes its bound values. NullPool: nothing outlives the job."""
    return create_engine(url, poolclass=NullPool, hide_parameters=True, connect_args={"connect_timeout": 10})


def alembic_config(script_location: Path | str = DEFAULT_SCRIPT_LOCATION) -> Config:
    config = Config()
    config.set_main_option("script_location", str(script_location))
    return config


def script_directory(script_location: Path | str = DEFAULT_SCRIPT_LOCATION) -> ScriptDirectory:
    return ScriptDirectory.from_config(alembic_config(script_location))


def code_heads(script_location: Path | str = DEFAULT_SCRIPT_LOCATION) -> tuple[str, ...]:
    return tuple(script_directory(script_location).get_heads())


@lru_cache(maxsize=1)
def expected_heads() -> tuple[str, ...]:
    """This image's Alembic heads (read once: the migration files of an image never change)."""
    return code_heads()


def advisory_lock_is_held(connection: Connection) -> bool:
    """Whether THIS session still holds the migration lock (a dropped connection silently loses it)."""
    return bool(
        connection.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND granted AND pid = pg_backend_pid()"
                " AND classid = CAST(:high AS oid) AND objid = CAST(:low AS oid) AND objsubid = 1)"
            ),
            {"high": (MIGRATION_LOCK_KEY >> 32) & 0xFFFFFFFF, "low": MIGRATION_LOCK_KEY & 0xFFFFFFFF},
        ).scalar()
    )


def _quote(connection: Connection, identifier: str) -> str:
    return connection.dialect.identifier_preparer.quote(identifier)


def reconcile_runtime_grants(connection: Connection, runtime_role: str) -> None:
    """Make everything the migrations created usable by the runtime role, and everything they WILL create.

    Run by the migration role (the owner), so it can grant on its own objects and set ITS default privileges:
      * the runtime role gets SELECT/INSERT/UPDATE/DELETE on tables and USAGE/SELECT on sequences, nothing else (no
        TRUNCATE, REFERENCES, TRIGGER, no CREATE on the schema, no ownership): triggers and functions run as the
        caller and functions are executable by default, so ordinary DML needs nothing more;
      * `ALTER DEFAULT PRIVILEGES` covers objects created by FUTURE migrations (it applies to the role that runs it);
      * the revision table is the exception: the runtime role may read it (readiness) and may not change it.
    Idempotent. The two mechanisms overlap on purpose: the defaults serve a migration run by hand, the grants repair any
    object that predates the defaults.
    """
    role = _quote(connection, runtime_role)
    for statement in (
        f"GRANT USAGE ON SCHEMA public TO {role}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {role}",
    ):
        connection.execute(text(statement))
    if connection.execute(text("SELECT to_regclass('public.alembic_version') IS NOT NULL")).scalar():
        connection.execute(text(f"REVOKE INSERT, UPDATE, DELETE ON TABLE public.alembic_version FROM {role}"))
