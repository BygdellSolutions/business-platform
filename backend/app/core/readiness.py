"""Readiness: is THIS process able to serve THIS image's code? One invariant:

    the database's Alembic revision == this image's single Alembic head.

Anything else is unready: no database, no `alembic_version`, an old revision, a newer or unknown one, several rows,
several heads in the code, or any state that does not look like exactly that. It never migrates, and it never accepts
"a revision this code knows": an image older than the database is as unready as a database older than the image.

The answer is a coarse reason code for the server's own log; the HTTP response says only ready or not ready.
"""

import threading

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, OperationalError, SQLAlchemyError

from app.core import migration

READY = "ready"


def evaluate(engine: Engine, heads: tuple[str, ...]) -> str:
    """READY, or a short reason code (never a revision, a host or an exception message)."""
    if len(heads) != 1:
        return "code_heads"
    try:
        with engine.connect() as connection:
            connection.execute(text("SET LOCAL statement_timeout = 3000"))
            if not connection.execute(text("SELECT to_regclass('alembic_version') IS NOT NULL")).scalar():
                return "no_revision_table"
            revisions = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    except SQLAlchemyError as error:
        # A refused connection or a timeout is "unreachable"; a malformed revision table is "unexpected".
        return "database_unreachable" if _is_connection_problem(error) else "unexpected_state"
    except Exception:
        return "unexpected_state"
    if len(revisions) != 1:
        return "revision_rows"
    return READY if revisions[0] == heads[0] else "revision_mismatch"


def _is_connection_problem(error: SQLAlchemyError) -> bool:
    return isinstance(error, OperationalError) or (isinstance(error, DBAPIError) and error.connection_invalidated)


def check(engine: Engine) -> str:
    try:
        heads = migration.expected_heads()
    except Exception:
        return "code_heads"
    return evaluate(engine, heads)


# --- bounded and single-flight ---------------------------------------------------------------------------------------------------------------------

# A database that accepts a connection and then never answers (a paused or partitioned server) would leave a probe hanging
# in the thread pool, and probes keep coming (the orchestrator, a public readiness route). So a probe waits a bounded time,
# and while one is still running no second one is started: at most ONE worker thread can ever be stuck on readiness.
PROBE_TIMEOUT_SECONDS = 4.0
_probe_lock = threading.Lock()


def check_bounded(engine: Engine, timeout: float = PROBE_TIMEOUT_SECONDS) -> str:
    if not _probe_lock.acquire(blocking=False):
        return "check_in_progress"  # the previous probe has not come back: unready, without starting another
    outcome: list[str] = []

    def run() -> None:
        try:
            outcome.append(check(engine))
        finally:
            _probe_lock.release()

    worker = threading.Thread(target=run, name="readiness-probe", daemon=True)
    worker.start()
    worker.join(timeout)
    return outcome[0] if outcome else "check_timeout"
