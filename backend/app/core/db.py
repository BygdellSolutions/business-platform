from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.base import Base  # noqa: F401  (re-exported: the models import it from app.core.base)
from app.core.config import settings

# hide_parameters: a failed statement's exception text then carries the SQL but never the bound values (which can be
# passwords, tokens or personal data), whoever ends up logging that exception. connect_timeout bounds how long an
# unreachable database can hold a request or a readiness probe.
engine = create_engine(settings.database_url, pool_pre_ping=True, hide_parameters=True, connect_args={"connect_timeout": 5})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
