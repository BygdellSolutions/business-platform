"""The migration job: bring the database to this image's Alembic head, safely, as a SEPARATE step from the web process.

    python -m app.scripts.migrate

Runs with the MIGRATION credentials (MIGRATION_DATABASE_URL: the role that owns the schema), never the runtime role.
The web process never migrates, and neither does a worker: this is the only place a production schema changes.

  1. connect with the migration credentials (one session, used for both the lock and Alembic);
  2. take a fixed PostgreSQL advisory lock on that session (a second job waits, up to --lock-timeout, so concurrent
     jobs serialize; the lock is released when the session ends, however the job ends);
  3. read the database's current revision and REFUSE one this image does not know (a newer deployment already
     migrated it) or several revisions (a branched or tampered history);
  4. `alembic upgrade head` on that same session (never a downgrade);
  5. make the runtime role (RUNTIME_DB_ROLE) able to use everything the migrations created;
  6. only on a TEST database whose operator set RENUMBER_INVOICES to the confirmation phrase: renumber its issued
     invoices from 1001 (`app.scripts.renumber_invoices`);
  7. confirm the lock was held throughout, and exit 0.

Any failure exits non-zero and nothing is swallowed. Output is one JSON line per event; no credential, URL or bound
parameter is ever printed.

Exit codes: 0 migrated (or already at head); 1 migration failed; 2 configuration error; 3 lock not obtained in time;
4 refused (the database or the code is in a state this job will not touch).
"""

import argparse
import logging
import sys
import time
from pathlib import Path

from alembic import command
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.core import migration
from app.core.logging_config import configure_logging, log
from app.scripts import renumber_invoices as renumber

EXIT_OK, EXIT_FAILED, EXIT_CONFIG, EXIT_LOCK_TIMEOUT, EXIT_REFUSED = 0, 1, 2, 3, 4
DEFAULT_LOCK_TIMEOUT_SECONDS = 600
POLL_SECONDS = 0.5


class Refused(Exception):
    pass


class LockTimeout(Exception):
    pass


def _scrub(message: str, url: str) -> str:
    """The first line of an exception message with the password (and the whole URL) removed, for the log."""
    password = make_url(url).password
    for secret in (url, password):
        if secret:
            message = message.replace(secret, "***")
    return message.splitlines()[0][:300] if message else ""


def _acquire_lock(connection, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    announced = False
    while True:
        got = connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": migration.MIGRATION_LOCK_KEY}).scalar()
        connection.commit()  # the lock belongs to the SESSION; this only closes the transaction the statement opened
        if got:
            return
        if time.monotonic() >= deadline:
            raise LockTimeout
        if not announced:
            log(logging.INFO, "waiting_for_migration_lock")
            announced = True
        time.sleep(POLL_SECONDS)


def _check_state(connection, script_location: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    heads = migration.code_heads(script_location)
    if len(heads) != 1:
        raise Refused("this image has more than one Alembic head")
    current = tuple(MigrationContext.configure(connection).get_current_heads())
    connection.commit()
    if len(current) > 1:
        raise Refused("the database records more than one Alembic revision")
    script = migration.script_directory(script_location)
    for revision in current:
        try:
            known = script.get_revision(revision) is not None
        except Exception:  # noqa: BLE001  (Alembic raises for a revision it cannot locate)
            known = False
        if not known:
            raise Refused("the database is at a revision this image does not know (it was migrated by a newer release)")
    return heads, current


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate the database to this image's Alembic head.")
    parser.add_argument("--lock-timeout", type=float, default=DEFAULT_LOCK_TIMEOUT_SECONDS, help="seconds to wait for another migration to finish")
    parser.add_argument("--script-location", type=Path, default=migration.DEFAULT_SCRIPT_LOCATION, help="the Alembic script directory (tests use another)")
    args = parser.parse_args(argv)
    configure_logging("migrate")
    for noisy in ("alembic.runtime.migration",):
        logging.getLogger(noisy).setLevel(logging.INFO)
        logging.getLogger(noisy).addHandler(logging.getLogger("bp").handlers[0])

    settings = migration.MigrationSettings()
    url = settings.url()
    if not url:
        log(logging.ERROR, "configuration_error", detail="MIGRATION_DATABASE_URL is not set")
        return EXIT_CONFIG
    if settings.renumber_invoices and settings.renumber_invoices != renumber.CONFIRMATION:
        log(logging.ERROR, "configuration_error", detail="RENUMBER_INVOICES is set but is not the confirmation phrase")
        return EXIT_CONFIG
    if settings.app_env == "production" and not settings.runtime_db_role:
        log(logging.ERROR, "configuration_error", detail="RUNTIME_DB_ROLE is required in production")
        return EXIT_CONFIG

    try:
        make_url(url)
    except Exception:  # noqa: BLE001  (SQLAlchemy's message would quote the whole string, password included)
        log(logging.ERROR, "configuration_error", detail="MIGRATION_DATABASE_URL is not a valid database URL")
        return EXIT_CONFIG

    engine = migration.migration_engine(url)
    try:
        # ONE connection for everything: the advisory lock is a property of this session, so it is held exactly as long
        # as this block runs, and the migration below runs on the same session.
        with engine.connect() as connection:
            _acquire_lock(connection, args.lock_timeout)
            heads, current = _check_state(connection, args.script_location)
            log(logging.INFO, "migrating", from_revision=current[0] if current else None, to_revision=heads[0])
            config = migration.alembic_config(args.script_location)
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            connection.commit()
            if settings.runtime_db_role:
                migration.reconcile_runtime_grants(connection, settings.runtime_db_role)
                connection.commit()
            if settings.renumber_invoices == renumber.CONFIRMATION:
                # An operator step for a TEST database (see the module); still under the migration lock.
                log(logging.INFO, "invoices_renumbered", **renumber.renumber_invoices(connection))
                connection.commit()
            if not migration.advisory_lock_is_held(connection):
                raise RuntimeError("the migration lock was lost while migrating")
            log(logging.INFO, "migrated", revision=heads[0])
    except Refused as error:
        log(logging.ERROR, "migration_refused", detail=str(error))
        return EXIT_REFUSED
    except LockTimeout:
        log(logging.ERROR, "migration_lock_timeout", detail="another migration still holds the lock")
        return EXIT_LOCK_TIMEOUT
    except Exception as error:  # noqa: BLE001  (every failure is reported and exits non-zero; nothing is swallowed)
        log(logging.ERROR, "migration_failed", error_type=type(error).__name__, detail=_scrub(str(error), url))
        return EXIT_FAILED
    finally:
        engine.dispose()  # closing the session releases the advisory lock
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(run())
