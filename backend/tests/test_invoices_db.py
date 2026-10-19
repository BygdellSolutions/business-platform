"""What PostgreSQL itself guarantees about invoices, with raw SQL that bypasses the API.

Immutability is enforced in the application AND by invoice-local triggers; the references and
CHECKs make a wrong invoice impossible to store, whatever wrote it. Every refusal here happens
inside a savepoint, so the rest of the test can keep going.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CustomerType
from tests.factories import make_customer
from tests.invoicing_support import completed, draft_invoice, issue


def refused(db: Session, sql: str, match: str | None = None, **params) -> str:
    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            db.execute(text(sql), params)
    message = str(caught.value.orig)
    if match:
        assert match in message, message
    return message


def allowed(db: Session, sql: str, **params) -> int:
    with db.begin_nested():
        return db.execute(text(sql), params).rowcount


@pytest.fixture
def world(client, db_session, sales):
    """A draft invoice and an issued one (two transactions each have two lines)."""
    org = sales.org
    draft_tx = completed(db_session, org, sales.billing, lines=[{}, {"description": "Second"}])
    issued_tx = completed(db_session, org, sales.billing, lines=[{}, {"description": "Second"}])
    draft = draft_invoice(client, sales.headers, draft_tx)
    issued = issue(client, sales.headers, draft_invoice(client, sales.headers, issued_tx))
    return type("World", (), dict(org=org, sales=sales, db=db_session, draft=draft, issued=issued, draft_tx=draft_tx, issued_tx=issued_tx,
                                  d=uuid.UUID(draft["id"]), i=uuid.UUID(issued["id"])))


# --- an issued invoice is immutable ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "update invoices set description = 'changed' where id = :i",
        "update invoices set customer_name = 'Someone Else' where id = :i",
        "update invoices set customer_snapshot = '{}'::jsonb where id = :i",
        "update invoices set issuer_snapshot = '{}'::jsonb where id = :i",
        "update invoices set net_amount = 1, vat_amount = 0, gross_amount = 1 where id = :i",
        "update invoices set number = 99, number_text = '99' where id = :i",
        "update invoices set invoice_date = '2000-01-01' where id = :i",
        "update invoices set due_date = '2099-01-01' where id = :i",
        "update invoices set version = version + 1 where id = :i",
        "update invoices set status = 'draft', number = null, number_text = null, issued_at = null, issued_by = null where id = :i",
        "delete from invoices where id = :i",
    ],
)
def test_an_issued_invoice_cannot_be_changed_or_deleted_by_raw_sql(world, statement):
    refused(world.db, statement, "issued invoice cannot be", i=world.i)


@pytest.mark.parametrize(
    "statement",
    [
        "update invoice_lines set description = 'changed' where invoice_id = :i",
        "update invoice_lines set fields = '[{\"x\": 1}]'::jsonb where invoice_id = :i",
        "update invoice_lines set unit_price_ex_vat = 1000.00, net_amount = 1000.00, vat_amount = 250.00, gross_amount = 1250.00 where invoice_id = :i and position = 1",
        "delete from invoice_lines where invoice_id = :i",
        "update invoice_transactions set fields = '[{\"x\": 1}]'::jsonb where invoice_id = :i",
        "update invoice_transactions set source_version = 5 where invoice_id = :i",
        "delete from invoice_transactions where invoice_id = :i",
        "update invoice_vat_rows set vat_amount = vat_amount + 1 where invoice_id = :i",
        "delete from invoice_vat_rows where invoice_id = :i",
    ],
)
def test_the_documents_of_an_issued_invoice_cannot_be_changed_or_deleted_by_raw_sql(world, statement):
    refused(world.db, statement, "issued invoice cannot be changed", i=world.i)


def test_nothing_can_be_added_to_an_issued_invoice(world):
    src = world.issued_tx.id
    refused(
        world.db,
        "insert into invoice_vat_rows (organization_id, invoice_id, vat_rate, net_amount, vat_amount) values (:o, :i, 6.00, 1, 0)",
        "issued invoice cannot be changed", o=world.org.id, i=world.i,
    )
    refused(
        world.db,
        "insert into invoice_transactions (organization_id, invoice_id, transaction_id, customer_id, currency, transaction_date, source_version, position) "
        "select organization_id, :i, id, billing_customer_id, currency, transaction_date, version, 9 from transactions where id = :t",
        "issued invoice cannot be changed", i=world.i, t=src,
    )


def test_the_issued_invoice_is_exactly_as_it_was_after_all_those_attempts(world, client):
    for statement in ("update invoices set description = 'x' where id = :i", "delete from invoice_lines where invoice_id = :i"):
        refused(world.db, statement, i=world.i)
    assert client.get(f"/api/invoices/{world.i}", headers=world.sales.headers).json() == world.issued


# --- a draft is mutable only where that is supported ---------------------------------------------------------------------------------


def test_a_draft_header_can_change_but_not_its_customer_currency_series_or_totals(world):
    d = world.d
    assert allowed(world.db, "update invoices set description = 'ok', invoice_date = '2026-10-02', due_date = '2026-11-01', version = version + 1 where id = :i", i=d) == 1
    other = make_customer(world.db, world.org, "Other", CustomerType.PERSON)
    refused(world.db, "update invoices set customer_id = :c where id = :i", "cannot be changed", i=d, c=other.id)
    refused(world.db, "update invoices set currency = 'EUR' where id = :i", "cannot be changed", i=d)
    refused(world.db, "update invoices set series = 'other' where id = :i", "cannot be changed", i=d)
    refused(world.db, "update invoices set net_amount = net_amount + 1, gross_amount = gross_amount + 1 where id = :i", "cannot be changed", i=d)
    refused(world.db, "update invoices set organization_id = :o where id = :i", "cannot be changed", i=d, o=uuid.uuid4())


def test_a_draft_document_changes_only_in_its_snapshot_content(world):
    d = world.d
    assert allowed(world.db, "update invoice_lines set fields = '[]'::jsonb where invoice_id = :i", i=d) == 2
    assert allowed(world.db, "update invoice_transactions set fields = '[]'::jsonb where invoice_id = :i", i=d) == 1
    for statement in (
        "update invoice_lines set description = 'x' where invoice_id = :i",
        "update invoice_lines set position = position + 10 where invoice_id = :i",
        "update invoice_transactions set source_version = 7 where invoice_id = :i",
        "update invoice_transactions set transaction_date = '2000-01-01' where invoice_id = :i",
        "update invoice_vat_rows set net_amount = 1 where invoice_id = :i",
    ):
        refused(world.db, statement, "only snapshot content", i=d)


def test_a_document_row_cannot_be_moved_to_another_invoice(world):
    refused(world.db, "update invoice_lines set invoice_id = :other where invoice_id = :i and position = 1", i=world.d, other=world.i)


def test_deleting_a_draft_removes_everything_that_belongs_to_it_and_nothing_else(world):
    assert allowed(world.db, "delete from invoices where id = :i", i=world.d) == 1
    for table in ("invoice_lines", "invoice_transactions", "invoice_vat_rows"):
        assert world.db.scalar(text(f"select count(*) from {table} where invoice_id = :i"), {"i": world.d}) == 0
        assert world.db.scalar(text(f"select count(*) from {table} where invoice_id = :i"), {"i": world.i}) > 0


# --- constraints ---------------------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement, constraint",
    [
        ("update invoices set status = 'void' where id = :i", "ck_invoices_status"),
        ("update invoices set status = 'issued' where id = :i", "ck_invoices_issued_has_number"),
        ("update invoices set number = 4, number_text = '4' where id = :i", "ck_invoices_draft_has_no_number"),
        ("update invoices set due_date = invoice_date - 1 where id = :i", "ck_invoices_due_after_invoice_date"),
        ("update invoices set version = 0 where id = :i", "ck_invoices_version_positive"),
        ("update invoices set customer_snapshot = '[]'::jsonb where id = :i", "ck_invoices_snapshots_are_objects"),
        ("update invoices set series = '' where id = :i", "ck_invoices_series_not_empty"),
    ],
)
def test_the_invoice_checks(world, statement, constraint):
    # Triggers off for this savepoint so the CHECK itself is what answers.
    world.db.execute(text("alter table invoices disable trigger user"))
    refused(world.db, statement, constraint, i=world.d)


def test_a_number_is_unique_per_organization_and_series_and_nothing_else_is_unique(world):
    db = world.db
    db.execute(text("alter table invoices disable trigger user"))
    # Same organization, same series, same number: refused.
    refused(db, "update invoices set status = 'issued', number = :n, number_text = 'X', issued_at = now(), issued_by = (select issued_by from invoices where id = :i) where id = :d",
            "uq_invoices_organization_series_number", n=world.issued["number"], i=world.i, d=world.d)
    # A different series, or a different organization, may reuse the number; the text label is not unique at all.
    issued_by = db.scalar(text("select issued_by from invoices where id = :i"), {"i": world.i})
    assert allowed(db, "update invoices set status = 'issued', series = series, number = :n + 1, number_text = :t, issued_at = now(), issued_by = :u where id = :d",
                   n=world.issued["number"], t=world.issued["number_text"], u=issued_by, d=world.d) == 1  # same number_text as the other invoice


def test_numbers_may_repeat_across_organizations(client, db_session):
    from tests.invoicing_support import Two

    two = Two(db_session)
    a = issue(client, two.a, draft_invoice(client, two.a, two.a_tx))
    b = issue(client, two.b, draft_invoice(client, two.b, two.b_tx))
    assert a["number"] == b["number"] == 1001 and a["number_text"] == b["number_text"]


@pytest.mark.parametrize(
    "statement, constraint",
    [
        ("update invoice_lines set quantity = 0, net_amount = 0, vat_amount = 0, gross_amount = 0 where invoice_id = :i and position = 1", "ck_invoice_lines_quantity_positive"),
        ("update invoice_lines set unit_price_ex_vat = -1, net_amount = -1, vat_amount = -0.25, gross_amount = -1.25 where invoice_id = :i and position = 1", "ck_invoice_lines_price_nonnegative"),
        ("update invoice_lines set vat_rate = 101, vat_amount = 858.50, gross_amount = 1708.50 where invoice_id = :i and position = 1", "ck_invoice_lines_vat_rate_range"),
        ("update invoice_lines set net_amount = 851.00, vat_amount = 212.75, gross_amount = 1063.75 where invoice_id = :i and position = 1", "ck_invoice_lines_net_amount"),
        ("update invoice_lines set vat_amount = vat_amount + 1, gross_amount = gross_amount + 1 where invoice_id = :i and position = 1", "ck_invoice_lines_vat_amount"),
        ("update invoice_lines set gross_amount = gross_amount + 1 where invoice_id = :i and position = 1", "ck_invoice_lines_gross_amount"),
        ("update invoice_lines set fields = '{}'::jsonb where invoice_id = :i and position = 1", "ck_invoice_lines_fields_array"),
    ],
)
def test_invoice_lines_keep_the_same_consistency_rules_as_sales_lines(world, statement, constraint):
    world.db.execute(text("alter table invoice_lines disable trigger user"))
    refused(world.db, statement, constraint, i=world.d)


# --- references -------------------------------------------------------------------------------------------------------------------------------------


def insert_link_sql() -> str:
    return (
        "insert into invoice_transactions (organization_id, invoice_id, transaction_id, customer_id, currency, transaction_date, source_version, position) "
        "select :o, :i, t.id, coalesce(:c, t.billing_customer_id), coalesce(:cur, t.currency, 'SEK'), t.transaction_date, t.version, :p from transactions t where t.id = :t"
    )


def insert_link(db, org, invoice, tx_id, *, customer=None, currency=None, position=9):
    return refused(db, insert_link_sql(), o=org, i=invoice, t=tx_id, c=customer, cur=currency, p=position)


# Invoicing by delivery (2026-10-11): an order may be on several invoices (one per delivery); an order LINE never on
# invoices for more, in total, than its quantity.
COPY_FIRST_ISSUED_LINE = (
    "insert into invoice_lines (organization_id, invoice_id, source_transaction_id, source_line_id, position, description, unit, quantity,"
    " unit_price_ex_vat, vat_rate, net_amount, vat_amount, gross_amount) "
    "select organization_id, :d, source_transaction_id, source_line_id, 99, description, unit, :q, unit_price_ex_vat, vat_rate,"
    " round(:q * unit_price_ex_vat, 2), round(round(:q * unit_price_ex_vat, 2) * vat_rate / 100, 2),"
    " round(:q * unit_price_ex_vat, 2) + round(round(:q * unit_price_ex_vat, 2) * vat_rate / 100, 2)"
    " from invoice_lines where invoice_id = :i and position = 1"
)


def test_an_order_can_be_on_several_invoices_but_a_line_never_beyond_its_quantity(world):
    db = world.db
    allowed(db, insert_link_sql(), o=world.org.id, i=world.d, t=world.issued_tx.id, c=None, cur=None, p=9)  # a second invoice of the order
    # Its first line is already invoiced in full (quantity 1) on the issued invoice: not even a part more.
    refused(db, COPY_FIRST_ISSUED_LINE, "an order line cannot be invoiced for more than was ordered", d=world.d, i=world.i, q="0.5")


def test_the_line_backstop_counts_every_invoice_and_allows_what_is_left(world):
    db = world.db
    db.execute(text("alter table invoice_lines disable trigger trg_invoice_lines_immutability"))
    db.execute(text("update invoice_lines set quantity = 0.5, net_amount = round(0.5 * unit_price_ex_vat, 2),"
                    " vat_amount = round(round(0.5 * unit_price_ex_vat, 2) * vat_rate / 100, 2),"
                    " gross_amount = round(0.5 * unit_price_ex_vat, 2) + round(round(0.5 * unit_price_ex_vat, 2) * vat_rate / 100, 2)"
                    " where invoice_id = :i and position = 1"), {"i": world.i})
    db.execute(text("alter table invoice_lines enable trigger trg_invoice_lines_immutability"))
    allowed(db, insert_link_sql(), o=world.org.id, i=world.d, t=world.issued_tx.id, c=None, cur=None, p=9)
    # Half is invoiced: the other half fits, a hair more does not.
    refused(db, COPY_FIRST_ISSUED_LINE, "an order line cannot be invoiced for more than was ordered", d=world.d, i=world.i, q="0.501")
    assert allowed(db, COPY_FIRST_ISSUED_LINE, d=world.d, i=world.i, q="0.5") == 1


def test_a_link_must_agree_with_the_transaction_on_customer_and_currency(world):
    db = world.db
    free = completed(db, world.org, world.sales.billing)
    other = make_customer(db, world.org, "Other", CustomerType.PERSON)
    # The link claims another customer: the invoice's own key (customer, currency) or the transaction's rejects it.
    assert "violates foreign key" in insert_link(db, world.org.id, world.d, free.id, customer=other.id)
    assert "violates foreign key" in insert_link(db, world.org.id, world.d, free.id, currency="EUR")


def test_a_transaction_without_a_currency_can_never_be_linked(world):
    db = world.db
    old = completed(db, world.org, world.sales.billing, currency=None)
    message = insert_link(db, world.org.id, world.d, old.id, currency="SEK")  # even claiming a currency
    assert "violates foreign key" in message


def test_nothing_links_across_organizations(client, db_session):
    from tests.invoicing_support import Two

    two = Two(db_session)
    invoice = draft_invoice(client, two.a, two.a_tx)
    # Org B's transaction on org A's invoice, rows labelled with either organization.
    for org in (two.a_org.id, two.b_org.id):
        assert "violates foreign key" in insert_link(db_session, org, uuid.UUID(invoice["id"]), two.b_tx.id, customer=two.a_customer.id)


def test_an_invoice_line_must_come_from_a_line_of_its_own_source_transaction(world):
    db = world.db
    # Point a draft line at a line that belongs to the OTHER transaction.
    db.execute(text("alter table invoice_lines disable trigger user"))
    foreign_line = db.scalar(text("select id from transaction_lines where transaction_id = :t limit 1"), {"t": world.issued_tx.id})
    message = refused(db, "update invoice_lines set source_line_id = :l where invoice_id = :i and position = 1", i=world.d, l=foreign_line)
    assert "fk_invoice_lines_source_line" in message or "uq_invoice_lines_source_line_once" in message


def test_moving_a_line_onto_another_source_line_is_held_to_its_quantity(world):
    db = world.db
    db.execute(text("alter table invoice_lines disable trigger trg_invoice_lines_immutability"))
    # Line 1 of the draft would become a second copy of line 2's source line, which is invoiced in full already.
    refused(db, "update invoice_lines set source_line_id = (select source_line_id from invoice_lines where invoice_id = :i and position = 2) where invoice_id = :i and position = 1",
            "an order line cannot be invoiced for more than was ordered", i=world.d)


def test_customers_transactions_and_lines_cannot_be_deleted_while_an_invoice_refers_to_them(world):
    db = world.db
    for statement in (
        "delete from customers where id = :c",
        "delete from transactions where id = :t",
        "delete from transaction_lines where transaction_id = :t",
    ):
        refused(db, statement, "violates foreign key", c=world.sales.billing.id, t=world.issued_tx.id)
        refused(db, statement, "violates foreign key", c=world.sales.billing.id, t=world.draft_tx.id)


def test_invoicing_needs_no_item_horse_or_custom_field_records_to_exist(world):
    """No foreign key from an invoicing table to anything that supplies document text."""
    rows = world.db.execute(
        text(
            "select distinct c.conrelid::regclass::text, c.confrelid::regclass::text from pg_constraint c "
            "where c.contype = 'f' and c.conrelid::regclass::text like 'invoice%'"
        )
    ).all()
    referenced = {target for _, target in rows}
    assert referenced <= {
        "organizations", "customers", "transactions", "transaction_lines", "users", "invoices", "invoice_transactions", "invoice_payments",
        "invoice_lines", "invoice_returns", "credit_notes",
    }
    assert not referenced & {"items", "horses", "custom_field_definitions", "custom_field_options", "custom_field_values"}


def test_deleting_items_and_field_definitions_changes_nothing_in_an_issued_invoice(world, client):
    from tests.factories import make_definition, make_item

    item = make_item(world.db, world.org, "Temp item")
    definition = make_definition(world.db, world.org, key="temp", show_on_invoice=True)
    world.db.execute(text("delete from custom_field_definitions where id = :d"), {"d": definition.id})
    world.db.execute(text("delete from items where id = :i"), {"i": item.id})
    assert client.get(f"/api/invoices/{world.i}", headers=world.sales.headers).json() == world.issued


# --- Sales has no database-level knowledge of invoices ------------------------------------------------------------------------------------


def test_no_trigger_on_a_sales_table_involves_invoicing(world):
    db = world.db
    triggers = db.execute(
        text(
            "select c.relname, t.tgname, p.proname from pg_trigger t join pg_class c on c.oid = t.tgrelid "
            "join pg_proc p on p.oid = t.tgfoid where not t.tgisinternal and c.relname in ('transactions', 'transaction_lines', 'customers', 'items')"
        )
    ).all()
    assert {(r[0], r[1]) for r in triggers} == {
        ("transactions", "trg_transactions_currency_immutable"),
        # Record numbers (core): handed out on insert, never changed; nothing to do with invoicing.
        ("transactions", "transactions_record_number"),
        ("customers", "customers_record_number"),
        ("items", "items_record_number"),
    }
    assert all("invoice" not in r[2] for r in triggers)


def test_only_invoice_tables_have_invoice_triggers_and_the_functions_know_only_invoice_tables(world):
    db = world.db
    on = db.execute(
        text("select distinct c.relname from pg_trigger t join pg_class c on c.oid = t.tgrelid join pg_proc p on p.oid = t.tgfoid where p.proname like 'invoice%' and not t.tgisinternal")
    ).scalars().all()
    assert sorted(on) == ["invoice_lines", "invoice_payments", "invoice_pdfs", "invoice_transactions", "invoice_vat_rows", "invoices"]
    bodies = db.execute(text("select proname, prosrc from pg_proc where proname in ('invoices_immutability', 'invoice_children_immutability', 'invoice_pdfs_guard')")).all()
    assert len(bodies) == 3
    for name, source in bodies:
        for forbidden in ("transactions ", "transaction_lines", "customers", "items", "custom_field"):
            assert forbidden not in source.replace("invoice_transactions", ""), (name, forbidden)
