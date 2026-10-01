from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from app.core.db import engine


@pytest.fixture
def db_session() -> Iterator[Session]:
    """A session whose work is always rolled back, so tests leave no data behind.

    Requires the database to be migrated (`alembic upgrade head`).
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
