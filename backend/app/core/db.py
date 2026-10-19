from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.base import Base  # noqa: F401  (re-exported: the models import it from app.core.base)
from app.core.config import Settings, settings


def make_engine(url: str, config: Settings = settings, **overrides: object) -> Engine:
    """The runtime engine: a bounded pool and server-side timeouts on every connection.

    hide_parameters: a failed statement's exception text then carries the SQL but never the bound values (which can be
    passwords, tokens or personal data), whoever ends up logging that exception. connect_timeout bounds how long an
    unreachable database can hold a request or a readiness probe. The pool is finite (a request that cannot get a connection
    within the timeout is refused, see `main.pool_exhausted`) and the server ends what runs, waits or idles for too long.
    The timeouts travel as startup options, so they hold for every statement, including ones that pass the ORM.
    """
    options = (
        f"-c statement_timeout={config.db_statement_timeout_ms} "
        f"-c lock_timeout={config.db_lock_timeout_ms} "
        f"-c idle_in_transaction_session_timeout={config.db_idle_in_transaction_timeout_ms}"
    )
    arguments: dict[str, object] = dict(
        pool_pre_ping=True,
        hide_parameters=True,
        pool_size=config.db_pool_size,
        max_overflow=config.db_max_overflow,
        pool_timeout=config.db_pool_timeout_seconds,
        connect_args={"connect_timeout": 5, "options": options},
    )
    arguments.update(overrides)
    return create_engine(url, **arguments)


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
