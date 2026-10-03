"""The currency / profile migrations on a database that already holds financial data.

A scratch database on the TEST server is migrated to the revision before currencies existed,
filled with organizations, customers and transactions that have no currency, and then migrated
forward, back and forward again. The point is what the migration does NOT do: it never labels
existing organizations or transactions with an assumed currency (no SEK, no anything).
"""

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DataError, IntegrityError

from app.scripts import reset_test_db

BACKEND_DIR = Path(__file__).resolve().parents[1]
BEFORE = "c41a7e5d9b20"  # the last revision without currencies or profiles
PROFILES = "d52b8f1a7c34"
HEAD = "e63c9a2b8d45"


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    # A separate process: the application in this process is configured for the main test database.
    env = {**os.environ, "DATABASE_URL": url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(BACKEND_DIR / "alembic.ini"), *args],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True, check=False,
    )


def _upgrade(url: str, revision: str) -> None:
    result = _alembic(url, "upgrade", revision)
    assert result.returncode == 0, result.stderr[-2000:]


def _downgrade(url: str, revision: str) -> None:
    result = _alembic(url, "downgrade", revision)
    assert result.returncode == 0, result.stderr[-2000:]


@pytest.fixture
def scratch_url() -> Iterator[str]:
    main = make_url(reset_test_db.test_database_url())
    # Ends in "_test" like every database the guard accepts.
    name = f"migration_{main.database}"
    scratch = main.set(database=name)
    url = scratch.render_as_string(hide_password=False)
    reset_test_db.assert_is_test_database(url)
    admin = create_engine(main.render_as_string(hide_password=False), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield url
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


class Existing:
    """Rows inserted while the schema has no currency columns."""

    def __init__(self) -> None:
        self.org_a, self.org_b = uuid.uuid4(), uuid.uuid4()
        self.customer_a, self.customer_b = uuid.uuid4(), uuid.uuid4()
        self.draft, self.completed, self.cancelled, self.other = (uuid.uuid4() for _ in range(4))


def _insert_existing(url: str) -> Existing:
    e = Existing()
    engine = create_engine(url)
    with engine.begin() as c:
        for org, name in ((e.org_a, "Old Org A"), (e.org_b, "Old Org B")):
            c.execute(text("INSERT INTO organizations (id, name) VALUES (:i, :n)"), {"i": org, "n": name})
        for cid, org in ((e.customer_a, e.org_a), (e.customer_b, e.org_b)):
            c.execute(
                text("INSERT INTO customers (id, organization_id, customer_type, name) VALUES (:i, :o, 'company', 'Umeå HK')"),
                {"i": cid, "o": org},
            )
        for tid, org, cid, status in (
            (e.draft, e.org_a, e.customer_a, "draft"),
            (e.completed, e.org_a, e.customer_a, "completed"),
            (e.cancelled, e.org_a, e.customer_a, "cancelled"),
            (e.other, e.org_b, e.customer_b, "completed"),
        ):
            c.execute(
                text(
                    "INSERT INTO transactions (id, organization_id, billing_customer_id, transaction_date, status) "
                    "VALUES (:i, :o, :c, '2026-01-15', :s)"
                ),
                {"i": tid, "o": org, "c": cid, "s": status},
            )
            c.execute(
                text(
                    "INSERT INTO transaction_lines (id, organization_id, transaction_id, position, description, unit, "
                    "quantity, unit_price_ex_vat, vat_rate, net_amount, vat_amount, gross_amount) "
                    "VALUES (gen_random_uuid(), :o, :t, 1, 'Horse massage', 'session', 1, 850.00, 25.00, 850.00, 212.50, 1062.50)"
                ),
                {"o": org, "t": tid},
            )
    engine.dispose()
    return e


def _scalar(url: str, sql: str, **params):
    engine = create_engine(url)
    try:
        with engine.connect() as c:
            return c.execute(text(sql), params).scalar()
    finally:
        engine.dispose()


def _execute(url: str, sql: str, **params) -> None:
    engine = create_engine(url)
    try:
        with engine.begin() as c:
            c.execute(text(sql), params)
    finally:
        engine.dispose()


def _columns(url: str, table: str) -> set[str]:
    engine = create_engine(url)
    try:
        with engine.connect() as c:
            rows = c.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"), {"t": table}
            )
            return {r[0] for r in rows}
    finally:
        engine.dispose()


def test_upgrade_never_assigns_a_currency_to_existing_data(scratch_url: str):
    _upgrade(scratch_url, BEFORE)
    assert "currency" not in _columns(scratch_url, "transactions")
    existing = _insert_existing(scratch_url)

    _upgrade(scratch_url, "head")

    # The new columns exist ...
    assert "currency" in _columns(scratch_url, "transactions")
    assert {"default_currency", "legal_name", "vat_number"} <= _columns(scratch_url, "organizations")
    # ... and EVERY existing organization and transaction is still without a currency. This is the
    # point of the migration: SEK (or any currency) is never assumed for financial data.
    assert _scalar(scratch_url, "SELECT count(*) FROM organizations WHERE default_currency IS NOT NULL") == 0
    assert _scalar(scratch_url, "SELECT count(*) FROM transactions WHERE currency IS NOT NULL") == 0
    assert _scalar(scratch_url, "SELECT count(*) FROM transactions") == 4
    assert _scalar(scratch_url, "SELECT count(*) FROM transactions WHERE currency = 'SEK'") == 0
    assert _scalar(scratch_url, "SELECT count(*) FROM organizations WHERE default_currency = 'SEK'") == 0
    # Profile columns start empty as well.
    assert _scalar(scratch_url, "SELECT count(*) FROM customers WHERE country_code IS NOT NULL OR city IS NOT NULL") == 0
    assert _scalar(scratch_url, "SELECT count(*) FROM organizations WHERE country_code IS NOT NULL OR legal_name IS NOT NULL") == 0

    # The financial data itself is untouched.
    assert _scalar(scratch_url, "SELECT sum(gross_amount) FROM transaction_lines") == 4 * 1062.50
    assert _scalar(scratch_url, "SELECT status FROM transactions WHERE id = :i", i=existing.completed) == "completed"
    assert _scalar(scratch_url, "SELECT version FROM transactions WHERE id = :i", i=existing.completed) == 1


def test_currency_rules_hold_on_migrated_data(scratch_url: str):
    _upgrade(scratch_url, BEFORE)
    existing = _insert_existing(scratch_url)
    _upgrade(scratch_url, "head")
    engine = create_engine(scratch_url)
    try:
        # NULL -> a currency is the one allowed transition ("assign an explicit currency") ...
        with engine.begin() as c:
            c.execute(text("UPDATE transactions SET currency = 'SEK' WHERE id = :i"), {"i": existing.completed})
        # ... but a currency, once set, can never change, not even to the same value's neighbour.
        with pytest.raises(IntegrityError, match="cannot be changed once set"):
            with engine.begin() as c:
                c.execute(text("UPDATE transactions SET currency = 'EUR' WHERE id = :i"), {"i": existing.completed})
        with pytest.raises(IntegrityError, match="cannot be changed once set"):
            with engine.begin() as c:
                c.execute(text("UPDATE transactions SET currency = NULL WHERE id = :i"), {"i": existing.completed})
        # Setting the same value again is not a change.
        with engine.begin() as c:
            c.execute(text("UPDATE transactions SET currency = 'SEK' WHERE id = :i"), {"i": existing.completed})
        # Shape checks.
        for bad in ("sek", "SE", "SEKK", "S3K", ""):
            with pytest.raises((IntegrityError, DataError)):
                with engine.begin() as c:
                    c.execute(text("UPDATE transactions SET currency = :c WHERE id = :i"), {"c": bad, "i": existing.draft})
        for bad in ("sek", "EURO", "S3K"):
            with pytest.raises((IntegrityError, DataError)):
                with engine.begin() as c:
                    c.execute(text("UPDATE organizations SET default_currency = :c WHERE id = :i"), {"c": bad, "i": existing.org_a})
        for bad in ("swe", "S", "s1"):
            with pytest.raises((IntegrityError, DataError)):
                with engine.begin() as c:
                    c.execute(text("UPDATE customers SET country_code = :c WHERE id = :i"), {"c": bad, "i": existing.customer_a})
        # The line key for later references is unique per (organization, line, transaction).
        with engine.connect() as c:
            names = {
                r[0]
                for r in c.execute(
                    text("SELECT conname FROM pg_constraint WHERE conrelid = 'transaction_lines'::regclass AND contype = 'u'")
                )
            }
        assert "uq_transaction_lines_org_id_transaction" in names
    finally:
        engine.dispose()


def test_downgrade_and_reupgrade_keep_data_and_stay_currency_free(scratch_url: str):
    _upgrade(scratch_url, BEFORE)
    _insert_existing(scratch_url)
    _upgrade(scratch_url, "head")
    _execute(scratch_url, "UPDATE organizations SET default_currency = 'SEK', city = 'Umeå' WHERE name = 'Old Org A'")

    # One step back removes the trigger and the transaction currency, keeps the profiles.
    _downgrade(scratch_url, PROFILES)
    assert "currency" not in _columns(scratch_url, "transactions")
    assert _scalar(scratch_url, "SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_transactions_currency_immutable'") == 0
    assert _scalar(scratch_url, "SELECT count(*) FROM pg_proc WHERE proname = 'transactions_currency_is_immutable'") == 0
    assert "default_currency" in _columns(scratch_url, "organizations")

    # All the way back: profile columns are gone, the financial data is not.
    _downgrade(scratch_url, BEFORE)
    assert "default_currency" not in _columns(scratch_url, "organizations")
    assert "country_code" not in _columns(scratch_url, "customers")
    assert _scalar(scratch_url, "SELECT count(*) FROM transactions") == 4
    assert _scalar(scratch_url, "SELECT sum(gross_amount) FROM transaction_lines") == 4 * 1062.50

    # And forward again: still nothing assumed.
    _upgrade(scratch_url, "head")
    assert _scalar(scratch_url, "SELECT count(*) FROM transactions WHERE currency IS NOT NULL") == 0
    assert _scalar(scratch_url, "SELECT count(*) FROM organizations WHERE default_currency IS NOT NULL") == 0
    assert _scalar(scratch_url, "SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_transactions_currency_immutable'") == 1


def test_models_and_migrations_agree_at_head(scratch_url: str):
    _upgrade(scratch_url, "head")
    result = _alembic(scratch_url, "check")
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
