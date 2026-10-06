"""Test configuration. Tests run ONLY against the dedicated test database.

Before the application is imported, DATABASE_URL is pointed at TEST_DATABASE_URL (a separate
Postgres server, see docker-compose.yml) and the guard refuses anything that is not clearly a
test database or that is the development database. There is no fallback to the dev database:
if TEST_DATABASE_URL is missing, pytest stops. At the start of every session the test
database is rebuilt from scratch (drop, migrate to head), so every run is deterministic.
"""

import os

import pytest

from app.scripts import reset_test_db  # light: imports no application code

try:
    TEST_DATABASE_URL = reset_test_db.test_database_url()
    reset_test_db.assert_is_test_database(TEST_DATABASE_URL)
except reset_test_db.UnsafeDatabase as exc:
    pytest.exit(f"Refusing to run tests: {exc}", returncode=2)
# Set only by tests/test_db_roles.py, for a child pytest run: the SAME disposable test database, but connected as the
# restricted RUNTIME role (DML only), so the ordinary application tests prove the runtime role can do everything the app does.
RUNTIME_ROLE_URL = os.environ.get("TEST_RUNTIME_ROLE_URL")
if RUNTIME_ROLE_URL:
    try:
        reset_test_db.assert_is_test_database(RUNTIME_ROLE_URL)
    except reset_test_db.UnsafeDatabase as exc:
        pytest.exit(f"Refusing to run tests: {exc}", returncode=2)
os.environ["DATABASE_URL"] = RUNTIME_ROLE_URL or TEST_DATABASE_URL  # before app.core.config is first imported
os.environ["MIGRATION_DATABASE_URL"] = TEST_DATABASE_URL  # a developer's .env value must never redirect a migration to the dev database
os.environ.pop("BFF_INTERNAL_SECRET", None)  # tests are the documented unauthenticated development mode unless a test opts in
os.environ.setdefault("APP_ENV", "development")  # tests are development by definition (production refuses AUTH_MODE=dev)
os.environ.setdefault("AUTH_MODE", "dev")  # ... with the dev identity unless a test chooses otherwise (never rely on a developer's .env for it)

from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import engine, get_db
from app.main import app
from tests.auth_support import as_bff
from tests.versions import FreshVersionClient
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


@pytest.fixture(scope="session", autouse=True)
def fresh_test_database() -> None:
    """Drop and re-migrate the test database once per session (no seed: tests build their own data)."""
    if RUNTIME_ROLE_URL:
        # A role run: the database was bootstrapped (roles, grants) and migrated by the owner role already. PROVE that this
        # connection really is the restricted runtime role (a run as the owner or a superuser would prove nothing).
        from sqlalchemy import create_engine, text

        engine = create_engine(RUNTIME_ROLE_URL)
        try:
            with engine.connect() as connection:
                privileged = connection.execute(
                    text(
                        "select (select rolsuper or rolcreatedb or rolcreaterole or rolreplication or rolbypassrls from pg_roles where rolname = current_user)"
                        " or has_schema_privilege(current_user, 'public', 'CREATE')"
                        " or exists (select 1 from pg_class c join pg_roles r on r.oid = c.relowner where r.rolname = current_user)"
                        " or exists (select 1 from pg_database d join pg_roles r on r.oid = d.datdba where d.datname = current_database() and r.rolname = current_user)"
                    )
                ).scalar()
        finally:
            engine.dispose()
        if privileged:
            pytest.exit("Refusing to run: TEST_RUNTIME_ROLE_URL is not a restricted runtime role (superuser, creator, owner or schema CREATE).", returncode=2)
        return
    reset_test_db.reset(TEST_DATABASE_URL)


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
    """API client whose requests share the rollback-only test session. It sends the CURRENT
    If-Match version for Sales mutations (tests/versions.py), so tests about other things keep
    working; tests about versions use `raw_client` or pass their own If-Match."""
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield FreshVersionClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def raw_client(db_session: Session, dev_auth: None) -> Iterator[TestClient]:
    """Like `client`, but sends exactly what the test sends (no automatic If-Match)."""
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


# --- session authentication (AUTH_MODE=session) -------------------------------------------------------------------------------------------


@pytest.fixture
def session_mode(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Real authentication on, with cheap Argon2 parameters (the algorithm is the real one; only the cost is tiny).

    Everything else keeps the production defaults. The development identity is explicitly present in the
    configuration (DEV_USER_EMAIL) so tests can prove it is ignored.
    """
    from pydantic import SecretStr

    from app.core import passwords

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "auth_mode", "session")
    monkeypatch.setattr(settings, "dev_user_email", None)
    monkeypatch.setattr(settings, "security_key", SecretStr("test-security-key-" + "x" * 32))
    monkeypatch.setattr(settings, "argon2_memory_kib", 1024)
    monkeypatch.setattr(settings, "argon2_time_cost", 1)
    monkeypatch.setattr(settings, "argon2_parallelism", 1)
    monkeypatch.setattr(settings, "trust_client_ip_header", False)
    passwords._dummy.clear()
    passwords.configure_admission_from_settings()
    try:
        yield
    finally:
        monkeypatch.undo()
        passwords._dummy.clear()
        passwords.configure_admission_from_settings()


@pytest.fixture
def session_client(db_session: Session, session_mode: None) -> Iterator[TestClient]:
    """An API client in session mode whose requests share the rollback-only test session."""
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app, headers=as_bff())  # acts as the BFF when a test configured the internal secret
    finally:
        app.dependency_overrides.clear()
