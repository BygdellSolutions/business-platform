from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import engine, get_db
from app.main import app
from app.models import Role
from tests.factories import add_member, make_org, make_user


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


@pytest.fixture
def dev_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force dev identity on, regardless of the developer's .env."""
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "auth_mode", "dev")
    monkeypatch.setattr(settings, "dev_user_email", None)


@pytest.fixture
def client(db_session: Session, dev_auth: None) -> Iterator[TestClient]:
    """API client whose requests share the rollback-only test session."""
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def member(db_session: Session):
    """(organization, auth headers) for a user who belongs to exactly one organization."""
    org, user = make_org(db_session, "Solo"), make_user(db_session)
    add_member(db_session, org, user, Role.OWNER)
    return org, {"X-Dev-User-Email": user.email}
