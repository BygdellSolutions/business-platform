"""Migration b8d0f2a4c613 (invoicing by delivery): existing invoices keep every row as it was, and read as whole orders."""

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tests.test_migration_currency import _downgrade, _scalar, _upgrade, scratch_url  # noqa: F401

BEFORE = "a7c9e1f3b457"


def _run(url: str, statements: list[str], **params) -> None:
    """Several statements in one transaction (all or nothing)."""
    engine = create_engine(url)
    try:
        with engine.begin() as c:
            for statement in statements:
                c.execute(text(statement), params)
    finally:
        engine.dispose()


SECOND_INVOICE = [
    "insert into invoices (id, organization_id, customer_id, currency, customer_snapshot, issuer_snapshot, customer_name, invoice_date, net_amount, vat_amount, gross_amount) "
    "values (:n, :o, :c, 'SEK', '{}', '{}', 'Umeå HK', '2026-03-01', 100.00, 25.00, 125.00)",
    "insert into invoice_transactions (organization_id, invoice_id, transaction_id, customer_id, currency, transaction_date, source_version, position) "
    "values (:o, :n, :t, :c, 'SEK', '2026-01-15', 1, 1)",
    "insert into invoice_lines (organization_id, invoice_id, source_transaction_id, source_line_id, position, description, unit, quantity, unit_price_ex_vat, vat_rate, net_amount, vat_amount, gross_amount) "
    "values (:o, :n, :t, :l, 1, 'Saddle pad', 'pcs', 1, 100.00, 25.00, 100.00, 25.00, 125.00)",
]


def _issued_invoice(url: str) -> dict:
    """An organization with an order (one line, 3 units) invoiced whole and issued, as before invoicing by delivery."""
    ids = {name: uuid.uuid4() for name in ("org", "user", "customer", "tx", "line", "invoice")}
    engine = create_engine(url)
    with engine.begin() as c:
        c.execute(text("insert into organizations (id, name, default_currency) values (:o, 'Existing Org', 'SEK')"), {"o": ids["org"]})
        c.execute(text("insert into users (id, email, name) values (:u, 'existing@dev.test', 'Existing')"), {"u": ids["user"]})
        c.execute(text("insert into customers (id, organization_id, customer_type, name) values (:c, :o, 'company', 'Umeå HK')"), {"c": ids["customer"], "o": ids["org"]})
        c.execute(
            text("insert into transactions (id, organization_id, billing_customer_id, transaction_date, status, currency) values (:t, :o, :c, '2026-01-15', 'completed', 'SEK')"),
            {"t": ids["tx"], "o": ids["org"], "c": ids["customer"]},
        )
        c.execute(
            text(
                "insert into transaction_lines (id, organization_id, transaction_id, position, description, unit, quantity, unit_price_ex_vat, vat_rate, net_amount, vat_amount, gross_amount) "
                "values (:l, :o, :t, 1, 'Saddle pad', 'pcs', 3, 100.00, 25.00, 300.00, 75.00, 375.00)"
            ),
            {"l": ids["line"], "o": ids["org"], "t": ids["tx"]},
        )
        c.execute(
            text(
                "insert into invoices (id, organization_id, customer_id, currency, customer_snapshot, issuer_snapshot, customer_name, invoice_date, net_amount, vat_amount, gross_amount) "
                "values (:i, :o, :c, 'SEK', '{}', '{}', 'Umeå HK', '2026-02-01', 300.00, 75.00, 375.00)"
            ),
            {"i": ids["invoice"], "o": ids["org"], "c": ids["customer"]},
        )
        c.execute(
            text(
                "insert into invoice_transactions (organization_id, invoice_id, transaction_id, customer_id, currency, transaction_date, source_version, position) "
                "values (:o, :i, :t, :c, 'SEK', '2026-01-15', 1, 1)"
            ),
            {"o": ids["org"], "i": ids["invoice"], "t": ids["tx"], "c": ids["customer"]},
        )
        c.execute(
            text(
                "insert into invoice_lines (organization_id, invoice_id, source_transaction_id, source_line_id, position, description, unit, quantity, unit_price_ex_vat, vat_rate, net_amount, vat_amount, gross_amount) "
                "values (:o, :i, :t, :l, 1, 'Saddle pad', 'pcs', 3, 100.00, 25.00, 300.00, 75.00, 375.00)"
            ),
            {"o": ids["org"], "i": ids["invoice"], "t": ids["tx"], "l": ids["line"]},
        )
        c.execute(
            text("update invoices set status = 'issued', number = 1001, number_text = '1001', issued_at = now(), issued_by = :u where id = :i"),
            {"i": ids["invoice"], "u": ids["user"]},
        )
    engine.dispose()
    return ids


def _rows(url: str) -> list:
    engine = create_engine(url)
    try:
        with engine.connect() as c:
            return [
                c.execute(text("select id, quantity, net_amount, updated_at from invoice_lines order by id")).all(),
                c.execute(text("select id, transaction_id, updated_at from invoice_transactions order by id")).all(),
                c.execute(text("select id, status, number_text, updated_at from invoices order by id")).all(),
            ]
    finally:
        engine.dispose()


def test_upgrade_reads_existing_invoices_as_whole_orders_and_rewrites_no_row(scratch_url: str):
    _upgrade(scratch_url, BEFORE)
    ids = _issued_invoice(scratch_url)
    before = _rows(scratch_url)

    _upgrade(scratch_url, "head")

    assert _rows(scratch_url) == before  # no row was updated (updated_at included)
    line = f"select ordered_quantity, backordered_quantity from invoice_lines where source_line_id = '{ids['line']}'"
    assert _scalar(scratch_url, line.replace("ordered_quantity, backordered_quantity", "ordered_quantity is null")) is True  # invoiced whole: not assumed
    assert _scalar(scratch_url, line.replace("ordered_quantity, backordered_quantity", "backordered_quantity")) == 0
    assert _scalar(scratch_url, f"select completes_order from invoice_transactions where transaction_id = '{ids['tx']}'") is True
    # The old invoice still holds the whole line: nothing more of it can be invoiced.
    with pytest.raises(DBAPIError, match="cannot be invoiced for more than was ordered"):
        _run(scratch_url, SECOND_INVOICE, n=uuid.uuid4(), o=ids["org"], c=ids["customer"], t=ids["tx"], l=ids["line"])


def test_downgrade_restores_the_old_keys_and_refuses_once_an_order_is_split(scratch_url: str):
    _upgrade(scratch_url, BEFORE)
    ids = _issued_invoice(scratch_url)
    _upgrade(scratch_url, "head")

    _downgrade(scratch_url, BEFORE)
    assert _scalar(scratch_url, "select count(*) from pg_constraint where conname in ('uq_invoice_transactions_source_once', 'uq_invoice_lines_source_line_once')") == 2
    assert _scalar(scratch_url, "select count(*) from information_schema.tables where table_name = 'invoice_holds'") == 0

    # Up again, and invoice a second part of a line that was invoiced in part (2 of 3, then 1 of 3).
    _upgrade(scratch_url, "head")
    _run(
        scratch_url,
        [
            "alter table invoice_lines disable trigger user",
            "update invoice_lines set quantity = 2, net_amount = 200.00, vat_amount = 50.00, gross_amount = 250.00 where source_line_id = :l",
            "alter table invoice_lines enable trigger user",
        ],
        l=ids["line"],
    )
    _run(scratch_url, SECOND_INVOICE, n=uuid.uuid4(), o=ids["org"], c=ids["customer"], t=ids["tx"], l=ids["line"])
    with pytest.raises(Exception, match="an order is invoiced over several invoices"):
        _downgrade(scratch_url, BEFORE)
