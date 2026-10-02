from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import engine, get_db
from app.main import app
from app.models import CustomerType, Role
from app.modules.sales.models import TransactionLine
from tests.factories import (
    add_member,
    make_customer,
    make_definition,
    make_horse,
    make_item,
    make_org,
    make_transaction,
    make_user,
)


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


@pytest.fixture
def sales(db_session: Session, member):
    """One organization with a billing customer and the catalog item "Horse massage"
    (service, unit "session", 850.00 excl. VAT, 25.00 % VAT), plus auth headers."""
    org, headers = member
    return SimpleNamespace(
        org=org,
        headers=headers,
        billing=make_customer(db_session, org, "Umeå HK", CustomerType.COMPANY, None, None),
        item=make_item(db_session, org, "Horse massage", unit="session", price_ex_vat="850.00", vat_rate="25.00"),
    )


@pytest.fixture
def cf(db_session: Session, sales):
    """Custom fields on transaction lines in one organization: Owner (reference to a customer)
    and Horse (reference to a horse that depends on Owner), over a draft transaction with two
    lines. Customers Anna and Erik own the horses Kalle and Storm."""
    org = sales.org
    anna = make_customer(db_session, org, "Anna Andersson")
    erik = make_customer(db_session, org, "Erik Svensson", email=None)
    kalle = make_horse(db_session, org, "Kalle", owner=anna)
    storm = make_horse(db_session, org, "Storm", owner=erik)
    tx = make_transaction(db_session, org, billing_customer=sales.billing, lines=[{}, {}])
    owner = make_definition(
        db_session, org, key="owner", label="Owner", field_type="reference",
        reference_source="customer", position=10,
    )
    horse = make_definition(
        db_session, org, key="horse", label="Horse", field_type="reference",
        reference_source="horse", depends_on=owner, depends_on_filter="owner_customer_id", position=20,
    )
    lines = list(
        db_session.scalars(
            select(TransactionLine).where(TransactionLine.transaction_id == tx.id).order_by(TransactionLine.position)
        )
    )
    return SimpleNamespace(
        org=org, headers=sales.headers, billing=sales.billing, item=sales.item,
        anna=anna, erik=erik, kalle=kalle, storm=storm, tx=tx, lines=lines,
        owner=owner, horse=horse,
    )
