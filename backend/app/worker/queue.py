"""The Procrastinate application: where the queue lives (our PostgreSQL) and which modules define tasks."""

import procrastinate
from sqlalchemy.engine import make_url

from app.core.config import settings


def conninfo() -> str:
    """The SQLAlchemy URL as the libpq connection string the queue library wants (same database, same credentials)."""
    url = make_url(settings.database_url)
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


app = procrastinate.App(
    connector=procrastinate.PsycopgConnector(
        conninfo=conninfo(),
        # The worker's own limits: a runaway statement is cancelled, a session stuck inside a transaction is ended.
        kwargs={"options": f"-c statement_timeout={settings.db_statement_timeout_ms} -c idle_in_transaction_session_timeout={settings.db_idle_in_transaction_timeout_ms}"},
        max_size=settings.worker_concurrency + 3,  # one connection per running job, plus listening, fetching and heartbeat
    ),
    import_paths=[name.strip() for name in settings.worker_task_modules.split(",") if name.strip()],
)
