"""The Invoicing migration on a database that already holds organizations and transactions.

A scratch database on the TEST server is migrated to the revision before Invoicing and filled
with existing, completed, currency-bearing transactions. The migration must be purely additive:
it creates the invoicing tables and triggers, adds one unique key to `transactions`, and changes no
existing row. Downgrade removes exactly what the upgrade added, and the data survives both ways.
"""

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

# Reuse the scratch-database machinery of the currency migration test.
from tests.test_migration_currency import _alembic, _columns, _downgrade, _execute, _scalar, _upgrade, scratch_url  # noqa: F401

BEFORE_INVOICING = "e63c9a2b8d45"
INVOICE_TABLES = {"invoices", "invoice_transactions", "invoice_lines", "invoice_vat_rows", "invoice_counters", "invoice_pdfs"}


def _tables(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        with engine.connect() as c:
            return {r[0] for r in c.execute(text("select table_name from information_schema.tables where table_schema = 'public'"))}
    finally:
        engine.dispose()


def _insert_existing(url: str) -> dict:
    ids = {name: uuid.uuid4() for name in ("org", "user", "customer", "tx_done", "tx_draft", "tx_old")}
    engine = create_engine(url)
    with engine.begin() as c:
        c.execute(text("insert into organizations (id, name, default_currency) values (:o, 'Existing Org', 'SEK')"), {"o": ids["org"]})
        c.execute(text("insert into users (id, email, name) values (:u, 'existing@dev.test', 'Existing')"), {"u": ids["user"]})
        c.execute(text("insert into customers (id, organization_id, customer_type, name) values (:c, :o, 'company', 'Umeå HK')"), {"c": ids["customer"], "o": ids["org"]})
        for key, status, currency in (("tx_done", "completed", "SEK"), ("tx_draft", "draft", "SEK"), ("tx_old", "completed", None)):
            c.execute(
                text(
                    "insert into transactions (id, organization_id, billing_customer_id, transaction_date, status, currency) "
                    "values (:t, :o, :c, '2026-01-15', :s, :cur)"
                ),
                {"t": ids[key], "o": ids["org"], "c": ids["customer"], "s": status, "cur": currency},
            )
            c.execute(
                text(
                    "insert into transaction_lines (id, organization_id, transaction_id, position, description, unit, quantity, unit_price_ex_vat, vat_rate, net_amount, vat_amount, gross_amount) "
                    "values (gen_random_uuid(), :o, :t, 1, 'Old massage', 'session', 1, 850.00, 25.00, 850.00, 212.50, 1062.50)"
                ),
                {"o": ids["org"], "t": ids[key]},
            )
    engine.dispose()
    return ids


def _fingerprint(url: str) -> list:
    engine = create_engine(url)
    try:
        with engine.connect() as c:
            return [
                c.execute(text("select id, status, version, currency, billing_customer_id::text from transactions order by id")).all(),
                c.execute(text("select id, net_amount, vat_amount, gross_amount, description from transaction_lines order by id")).all(),
                c.execute(text("select id, name, default_currency from organizations order by id")).all(),
                c.execute(text("select id, name from customers order by id")).all(),
            ]
    finally:
        engine.dispose()


def test_upgrade_adds_invoicing_without_changing_any_existing_row(scratch_url: str):
    _upgrade(scratch_url, BEFORE_INVOICING)
    assert not INVOICE_TABLES & _tables(scratch_url)
    _insert_existing(scratch_url)
    before = _fingerprint(scratch_url)

    _upgrade(scratch_url, "head")

    assert INVOICE_TABLES <= _tables(scratch_url)
    assert _fingerprint(scratch_url) == before  # not a single existing value moved
    for table in INVOICE_TABLES:
        assert _scalar(scratch_url, f"select count(*) from {table}") == 0
    # No invoiced state leaked into Sales.
    assert not any("invoice" in column for column in _columns(scratch_url, "transactions") | _columns(scratch_url, "transaction_lines"))


def test_migrated_data_can_be_invoiced_by_the_new_tables_and_is_protected_by_them(scratch_url: str):
    _upgrade(scratch_url, BEFORE_INVOICING)
    ids = _insert_existing(scratch_url)
    _upgrade(scratch_url, "head")
    engine = create_engine(scratch_url)
    try:
        with engine.begin() as c:
            c.execute(
                text(
                    "insert into invoices (id, organization_id, customer_id, currency, customer_snapshot, issuer_snapshot, customer_name, invoice_date, net_amount, vat_amount, gross_amount) "
                    "values (:i, :o, :c, 'SEK', '{}', '{}', 'Umeå HK', '2026-02-01', 850.00, 212.50, 1062.50)"
                ),
                {"i": ids["org"], "o": ids["org"], "c": ids["customer"]},
            )
            c.execute(
                text(
                    "insert into invoice_transactions (organization_id, invoice_id, transaction_id, customer_id, currency, transaction_date, source_version, position) "
                    "values (:o, :i, :t, :c, 'SEK', '2026-01-15', 1, 1)"
                ),
                {"o": ids["org"], "i": ids["org"], "t": ids["tx_done"], "c": ids["customer"]},
            )
        # The historical transaction without a currency can never be linked.
        with pytest.raises(IntegrityError):
            with engine.begin() as c:
                c.execute(
                    text(
                        "insert into invoice_transactions (organization_id, invoice_id, transaction_id, customer_id, currency, transaction_date, source_version, position) "
                        "values (:o, :i, :t, :c, 'SEK', '2026-01-15', 1, 2)"
                    ),
                    {"o": ids["org"], "i": ids["org"], "t": ids["tx_old"], "c": ids["customer"]},
                )
        # Nor a transaction that is still a draft... at the database level a draft has the right
        # customer and currency, so this is the application's rule (tested through the API); what the
        # database does guarantee is that a transaction is on one invoice only.
        with pytest.raises(IntegrityError, match="uq_invoice_transactions_source_once"):
            with engine.begin() as c:
                c.execute(
                    text(
                        "insert into invoices (id, organization_id, customer_id, currency, customer_snapshot, issuer_snapshot, customer_name, invoice_date, net_amount, vat_amount, gross_amount) "
                        "values (gen_random_uuid(), :o, :c, 'SEK', '{}', '{}', 'Umeå HK', '2026-02-01', 0, 0, 0)"
                    ),
                    {"o": ids["org"], "c": ids["customer"]},
                )
                c.execute(
                    text(
                        "insert into invoice_transactions (organization_id, invoice_id, transaction_id, customer_id, currency, transaction_date, source_version, position) "
                        "select :o, id, :t, :c, 'SEK', '2026-01-15', 1, 1 from invoices where id <> :i and organization_id = :o"
                    ),
                    {"o": ids["org"], "i": ids["org"], "t": ids["tx_done"], "c": ids["customer"]},
                )
    finally:
        engine.dispose()


def test_downgrade_removes_exactly_what_the_upgrade_added_and_reupgrade_restores_it(scratch_url: str):
    _upgrade(scratch_url, BEFORE_INVOICING)
    _insert_existing(scratch_url)
    before = _fingerprint(scratch_url)
    _upgrade(scratch_url, "head")

    _downgrade(scratch_url, BEFORE_INVOICING)
    assert not INVOICE_TABLES & _tables(scratch_url)
    assert _scalar(scratch_url, "select count(*) from pg_proc where proname in ('invoices_immutability', 'invoice_children_immutability', 'invoice_pdfs_guard')") == 0
    assert _scalar(scratch_url, "select count(*) from pg_constraint where conname = 'uq_transactions_org_id_customer_currency'") == 0
    assert _fingerprint(scratch_url) == before

    _upgrade(scratch_url, "head")
    assert INVOICE_TABLES <= _tables(scratch_url)
    assert _scalar(scratch_url, "select count(*) from pg_trigger where tgname like 'trg_invoice%' and not tgisinternal") == 5
    assert _fingerprint(scratch_url) == before


def test_downgrade_works_even_with_an_issued_invoice_present(scratch_url: str):
    """Dropping the tables is not blocked by the immutability triggers (they guard rows, not tables)."""
    _upgrade(scratch_url, BEFORE_INVOICING)
    ids = _insert_existing(scratch_url)
    _upgrade(scratch_url, "head")
    _execute(
        scratch_url,
        "insert into invoices (id, organization_id, customer_id, currency, customer_snapshot, issuer_snapshot, customer_name, invoice_date, net_amount, vat_amount, gross_amount, status, number, number_text, issued_at, issued_by) "
        "values (gen_random_uuid(), :o, :c, 'SEK', '{}', '{}', 'Umeå HK', '2026-02-01', 0, 0, 0, 'issued', 1, '1', now(), :u)",
        o=ids["org"], c=ids["customer"], u=ids["user"],
    )
    _downgrade(scratch_url, BEFORE_INVOICING)
    assert not INVOICE_TABLES & _tables(scratch_url)


def test_models_and_migrations_still_agree_at_head(scratch_url: str):
    _upgrade(scratch_url, "head")
    result = _alembic(scratch_url, "check")
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
