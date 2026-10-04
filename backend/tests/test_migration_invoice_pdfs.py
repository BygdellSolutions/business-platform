"""The invoice PDF migration: purely additive, reversible, and in agreement with the models.

A scratch database on the TEST server is migrated to the revision before the PDF table and given an
issued invoice. The upgrade must add `invoice_pdfs` (with its trigger and function) and change no
existing row; the downgrade must remove exactly that, with or without stored artifacts.
"""

import hashlib
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tests.test_migration_currency import _alembic, _downgrade, _execute, _scalar, _upgrade, scratch_url  # noqa: F401

BEFORE_PDFS = "f74d0b3c9e56"
PDF_REVISION = "a85e1c4d7f90"


def _tables_of(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        with engine.connect() as c:
            return {r[0] for r in c.execute(text("select table_name from information_schema.tables where table_schema = 'public'"))}
    finally:
        engine.dispose()


def _issued_invoice(url: str) -> dict:
    ids = {name: uuid.uuid4() for name in ("org", "user", "customer", "invoice")}
    _execute(url, "insert into organizations (id, name, default_currency) values (:o, 'Existing Org', 'SEK')", o=ids["org"])
    _execute(url, "insert into users (id, email, name) values (:u, 'existing@dev.test', 'Existing')", u=ids["user"])
    _execute(url, "insert into customers (id, organization_id, customer_type, name) values (:c, :o, 'company', 'Umeå HK')", c=ids["customer"], o=ids["org"])
    _execute(
        url,
        "insert into invoices (id, organization_id, customer_id, currency, customer_snapshot, issuer_snapshot, customer_name, invoice_date, net_amount, vat_amount, gross_amount, status, number, number_text, issued_at, issued_by) "
        "values (:i, :o, :c, 'SEK', '{}', '{}', 'Umeå HK', '2026-02-01', 0, 0, 0, 'issued', 1, '1', now(), :u)",
        i=ids["invoice"], o=ids["org"], c=ids["customer"], u=ids["user"],
    )
    return ids


def _artifact(url: str, ids: dict) -> None:
    content = b"%PDF-1.4 scratch"
    _execute(
        url,
        "insert into invoice_pdfs (organization_id, invoice_id, content, byte_size, sha256, renderer, template_version, source_sha256) "
        "values (:o, :i, :c, :n, :h, 'r', 1, repeat('a', 64))",
        o=ids["org"], i=ids["invoice"], c=content, n=len(content), h=hashlib.sha256(content).hexdigest(),
    )


def _fingerprint(url: str) -> list:
    engine = create_engine(url)
    try:
        with engine.connect() as c:
            return [
                c.execute(text("select id, status, number_text, net_amount, gross_amount from invoices order by id")).all(),
                c.execute(text("select id, name from customers order by id")).all(),
                c.execute(text("select id, name from organizations order by id")).all(),
            ]
    finally:
        engine.dispose()


def test_upgrade_adds_only_the_artifact_table_with_its_guard_and_changes_no_row(scratch_url: str):
    _upgrade(scratch_url, BEFORE_PDFS)
    assert "invoice_pdfs" not in _tables_of(scratch_url)
    _issued_invoice(scratch_url)
    before = _fingerprint(scratch_url)

    _upgrade(scratch_url, PDF_REVISION)

    assert "invoice_pdfs" in _tables_of(scratch_url)
    assert _fingerprint(scratch_url) == before
    assert _scalar(scratch_url, "select count(*) from invoice_pdfs") == 0
    assert _scalar(scratch_url, "select count(*) from pg_trigger where tgname = 'trg_invoice_pdfs_guard' and not tgisinternal") == 1
    assert _scalar(scratch_url, "select count(*) from pg_proc where proname = 'invoice_pdfs_guard'") == 1


def test_the_guard_knows_only_the_invoices_table(scratch_url: str):
    _upgrade(scratch_url, PDF_REVISION)
    source = _scalar(scratch_url, "select prosrc from pg_proc where proname = 'invoice_pdfs_guard'")
    for live in ("transactions", "transaction_lines", "customers", "items", "organizations", "custom_field"):
        assert live not in source.replace("invoice_pdfs", ""), live
    assert "invoices" in source


def test_an_artifact_can_be_stored_once_and_never_changed_after_migrating(scratch_url: str):
    _upgrade(scratch_url, BEFORE_PDFS)
    ids = _issued_invoice(scratch_url)
    _upgrade(scratch_url, PDF_REVISION)
    _artifact(scratch_url, ids)
    for statement in ("update invoice_pdfs set renderer = 'x'", "delete from invoice_pdfs"):
        with pytest.raises(DBAPIError, match="frozen invoice PDF"):
            _execute(scratch_url, statement)
    with pytest.raises(DBAPIError):
        _artifact(scratch_url, ids)  # one per invoice


def test_downgrade_removes_exactly_the_artifact_table_and_reupgrade_restores_it(scratch_url: str):
    _upgrade(scratch_url, BEFORE_PDFS)
    _issued_invoice(scratch_url)
    before = _fingerprint(scratch_url)
    _upgrade(scratch_url, PDF_REVISION)

    _downgrade(scratch_url, BEFORE_PDFS)
    assert "invoice_pdfs" not in _tables_of(scratch_url)
    assert _scalar(scratch_url, "select count(*) from pg_proc where proname = 'invoice_pdfs_guard'") == 0
    assert _scalar(scratch_url, "select count(*) from pg_trigger where tgname = 'trg_invoice_pdfs_guard'") == 0
    assert _fingerprint(scratch_url) == before
    assert "invoices" in _tables_of(scratch_url)  # the invoice itself is untouched

    _upgrade(scratch_url, PDF_REVISION)
    assert "invoice_pdfs" in _tables_of(scratch_url)
    assert _fingerprint(scratch_url) == before


def test_downgrade_works_even_with_a_stored_artifact_present(scratch_url: str):
    """Dropping the table is not blocked by the row guard (it guards rows, not the table)."""
    _upgrade(scratch_url, BEFORE_PDFS)
    ids = _issued_invoice(scratch_url)
    _upgrade(scratch_url, PDF_REVISION)
    _artifact(scratch_url, ids)
    _downgrade(scratch_url, BEFORE_PDFS)
    assert "invoice_pdfs" not in _tables_of(scratch_url)
    assert _scalar(scratch_url, "select count(*) from invoices") == 1


def test_models_and_migrations_agree_at_the_pdf_revision_and_at_head(scratch_url: str):
    _upgrade(scratch_url, "head")
    result = _alembic(scratch_url, "check")
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert _scalar(scratch_url, "select version_num from alembic_version") == PDF_REVISION
