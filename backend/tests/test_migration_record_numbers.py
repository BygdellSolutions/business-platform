"""The record numbers migration on a database that already has data, including a DRAFT invoice (staging had one, and
the first version of the migration was refused by the invoice immutability trigger when it filled in the draft's
order number) and an ISSUED invoice (which must stay exactly as issued).
"""

import uuid

from tests.test_migration_currency import _alembic, _downgrade, _execute, _scalar, _upgrade, scratch_url  # noqa: F401

BEFORE = "d3f5b7c9e124"
NUMBERS = "e4a6c8d0f235"


def _invoice(url: str, org, customer, tx, status: str) -> uuid.UUID:
    invoice = uuid.uuid4()
    _execute(
        url,
        "insert into invoices (id, organization_id, customer_id, currency, customer_snapshot, issuer_snapshot, customer_name, invoice_date,"
        " net_amount, vat_amount, gross_amount) values (:i, :o, :c, 'SEK', '{}', '{}', 'Umeå HK', '2026-10-01', 0, 0, 0)",
        i=invoice, o=org, c=customer,
    )
    _execute(
        url,
        "insert into invoice_transactions (organization_id, invoice_id, transaction_id, customer_id, currency, transaction_date, source_version, position)"
        " values (:o, :i, :t, :c, 'SEK', '2026-10-01', 1, 1)",
        o=org, i=invoice, t=tx, c=customer,
    )
    if status == "issued":
        # Issued the way the database allows it; afterwards the row is immutable.
        user = uuid.uuid4()
        _execute(url, "insert into users (id, email, name) values (:u, :e, 'Issuer')", u=user, e=f"{user}@example.test")
        _execute(
            url, "update invoices set status = 'issued', number = 1, number_text = '1', issued_at = now(), issued_by = :u where id = :i", i=invoice, u=user
        )
    return invoice


def test_existing_records_are_numbered_and_only_draft_invoices_learn_their_orders_numbers(scratch_url: str):
    _upgrade(scratch_url, BEFORE)
    org = uuid.uuid4()
    _execute(scratch_url, "insert into organizations (id, name, default_currency) values (:o, 'Org', 'SEK')", o=org)
    customers = [uuid.uuid4(), uuid.uuid4()]
    for index, customer in enumerate(customers):
        _execute(
            scratch_url,
            "insert into customers (id, organization_id, customer_type, name, created_at) values (:c, :o, 'company', 'Umeå HK', now() + make_interval(secs => :s))",
            c=customer, o=org, s=index,
        )
    orders = [uuid.uuid4(), uuid.uuid4()]
    for index, tx in enumerate(orders):
        _execute(
            scratch_url,
            "insert into transactions (id, organization_id, billing_customer_id, transaction_date, status, currency, created_at)"
            " values (:t, :o, :c, '2026-10-01', 'completed', 'SEK', now() + make_interval(secs => :s))",
            t=tx, o=org, c=customers[0], s=index,
        )
    draft = _invoice(scratch_url, org, customers[0], orders[0], "draft")
    issued = _invoice(scratch_url, org, customers[0], orders[1], "issued")

    _upgrade(scratch_url, NUMBERS)

    assert [_scalar(scratch_url, "select number from customers where id = :c", c=c) for c in customers] == [1, 2]
    assert [_scalar(scratch_url, "select number from transactions where id = :t", t=t) for t in orders] == [1001, 1002]
    assert _scalar(scratch_url, "select transaction_number from invoice_transactions where invoice_id = :i", i=draft) == 1001
    assert _scalar(scratch_url, "select transaction_number from invoice_transactions where invoice_id = :i", i=issued) is None
    # The protection is back on after the backfill: an issued invoice's link still cannot change.
    refused = False
    try:
        _execute(scratch_url, "update invoice_transactions set transaction_number = 5 where invoice_id = :i", i=issued)
    except Exception as error:  # noqa: BLE001
        refused = "issued invoice" in str(error)
    assert refused
    # New records continue each series.
    _execute(scratch_url, "insert into customers (organization_id, customer_type, name) values (:o, 'person', 'Anna')", o=org)
    assert _scalar(scratch_url, "select max(number) from customers where organization_id = :o", o=org) == 3

    _downgrade(scratch_url, BEFORE)
    assert _scalar(scratch_url, "select count(*) from information_schema.columns where table_name = 'customers' and column_name = 'number'") == 0
