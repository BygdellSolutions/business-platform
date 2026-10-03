"""An issued invoice is a self-contained document.

Whatever happens to the live customer, organization, item, custom-field or source records after
issuance, reading the invoice returns the same document, and reading it does not even query them.
"""

import pytest
from sqlalchemy import select, text

from app.models import CustomerType
from app.modules.sales.models import TransactionLine
from tests.factories import make_customer, make_definition, make_item, make_value
from tests.invoicing_support import INVOICES, Statements, completed, draft_invoice, issue

@pytest.fixture
def issued_world(client, db_session, sales):
    """An issued invoice built from rich live data: item, profile, reference custom fields."""
    org = sales.org
    org.legal_name, org.address_line1, org.city, org.vat_number = "Solo AB", "Storgatan 1", "Umeå", "SE556000000101"
    customer = make_customer(db_session, org, "Umeå HK", CustomerType.COMPANY, "hk@example.test", "070-1", city="Umeå", country_code="SE", vat_number="SE1")
    item = make_item(db_session, org, "Horse massage")
    owner = make_customer(db_session, org, "Anna Andersson")
    tx = completed(db_session, org, customer, lines=[{"item": item, "description": "Horse massage"}, {"description": "Travel", "unit_price_ex_vat": "2.50"}])
    line = db_session.scalar(select(TransactionLine).where(TransactionLine.transaction_id == tx.id, TransactionLine.position == 1))
    reference = make_definition(db_session, org, key="owner", label="Owner", field_type="reference", reference_source="customer", show_on_invoice=True)
    select_field = make_definition(db_session, org, key="kind", label="Kind", field_type="select", show_on_invoice=True, options=["Therapy", "Check"])
    header = make_definition(db_session, org, entity_type="transaction", key="po", label="PO", field_type="text", show_on_invoice=True)
    from app.modules.custom_fields.models import CustomFieldOption

    option = db_session.scalar(select(CustomFieldOption).where(CustomFieldOption.definition_id == select_field.id, CustomFieldOption.label == "Therapy"))
    make_value(db_session, org, reference, line.id, value_reference_id=owner.id)
    make_value(db_session, org, select_field, line.id, value_option_id=option.id)
    make_value(db_session, org, header, tx.id, value_text="PO-17")
    document = issue(client, sales.headers, draft_invoice(client, sales.headers, tx))
    assert document["lines"][0]["fields"] and document["transactions"][0]["fields"]
    return type("W", (), dict(org=org, customer=customer, item=item, owner=owner, tx=tx, line=line, reference=reference,
                              select_field=select_field, option=option, header=header, document=document, headers=sales.headers, db=db_session))


def read(client, world) -> dict:
    return client.get(f"{INVOICES}/{world.document['id']}", headers=world.headers).json()


def test_changing_live_customer_organization_item_and_fields_does_not_change_the_document(client, issued_world):
    w = issued_world

    w.customer.name, w.customer.email, w.customer.city, w.customer.vat_number, w.customer.active = "Renamed Club", "new@example.test", "Luleå", "SE999", False
    w.org.name, w.org.legal_name, w.org.address_line1, w.org.vat_number = "Renamed Org", "Other AB", "Elsewhere 9", "SE000"
    w.item.name, w.item.price_ex_vat, w.item.vat_rate, w.item.unit, w.item.active = "Renamed item", 1, 6, "hour", False
    w.owner.name = "Renamed Owner"
    w.reference.label = "Renamed field"
    w.select_field.label = "Renamed kind"
    w.option.label = "Renamed option"
    w.header.label = "Renamed PO"
    w.db.flush()

    assert read(client, w) == w.document
    assert client.get(INVOICES, headers=w.headers).json()[0]["customer_name"] == "Umeå HK"


def test_changing_or_removing_the_sources_by_raw_sql_does_not_change_the_document(client, issued_world):
    w = issued_world
    for statement in (
        "update custom_field_definitions set enabled = false, show_on_invoice = false where organization_id = :o",
        "update custom_field_options set enabled = false where organization_id = :o",
        "update custom_field_values set value_text = 'changed' where organization_id = :o and value_text is not null",
        "delete from custom_field_values where organization_id = :o",
        "update transactions set transaction_date = '2000-01-01' where organization_id = :o",
    ):
        w.db.execute(text("alter table transactions disable trigger user"))
        w.db.execute(text(statement), {"o": w.org.id})
    w.db.execute(text("update customers set name = 'Gone', email = null, phone = null where organization_id = :o"), {"o": w.org.id})
    w.db.execute(text("update transaction_lines set description = 'Tampered' where organization_id = :o"), {"o": w.org.id})

    assert read(client, w) == w.document


def test_the_document_is_readable_even_when_every_live_record_is_gone(client, issued_world):
    w = issued_world
    # Delete the live customer, items, fields, transactions and lines altogether. (Foreign keys and
    # triggers are switched off for this rolled-back transaction: such a deletion is exactly what
    # they exist to prevent, and the point is that the document does not depend on any of it.)
    w.db.execute(text("set local session_replication_role = replica"))
    for table in ("custom_field_values", "custom_field_options", "custom_field_definitions", "transaction_lines", "transactions", "items", "customers"):
        w.db.execute(text(f"delete from {table} where organization_id = :o"), {"o": w.org.id})

    assert read(client, w) == w.document
    listed = client.get(INVOICES, headers=w.headers).json()
    assert [row["customer_name"] for row in listed] == ["Umeå HK"]


def test_reading_an_issued_invoice_queries_no_live_table(client, issued_world):
    w = issued_world
    with Statements() as recorded:
        one = client.get(f"{INVOICES}/{w.document['id']}", headers=w.headers)
        listing = client.get(INVOICES, headers=w.headers)
        listing_filtered = client.get(INVOICES, params={"q": "umeå", "status": "issued"}, headers=w.headers)
    assert one.status_code == listing.status_code == listing_filtered.status_code == 200

    assert recorded.touching_live_tables() == []
    # ... and the recorder is not blind: the same reads did query the invoicing tables.
    joined = " ".join(recorded.sql).lower()
    for table in ("invoices", "invoice_lines", "invoice_transactions", "invoice_vat_rows"):
        assert table in joined, table


def test_the_recorder_does_see_live_table_reads_elsewhere(client, issued_world):
    """Control for the test above: a Sales read and the invoiceable list DO touch live tables."""
    w = issued_world
    with Statements() as recorded:
        client.get(f"/api/transactions/{w.tx.id}", headers=w.headers)
    assert recorded.touching_live_tables() != []
    with Statements() as recorded:
        client.get("/api/invoiceable-transactions", headers=w.headers)
    assert recorded.touching_live_tables() != []


def test_a_draft_read_is_also_free_of_live_tables(client, db_session, sales):
    draft = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing))
    with Statements() as recorded:
        assert client.get(f"{INVOICES}/{draft['id']}", headers=sales.headers).status_code == 200
    assert recorded.touching_live_tables() == []


def test_the_state_view_reads_invoicing_tables_only(client, issued_world):
    w = issued_world
    with Statements() as recorded:
        rows = client.get(f"{INVOICES}/by-transaction", params={"ids": str(w.tx.id)}, headers=w.headers).json()
    assert rows[0]["state"] == "invoiced"
    assert recorded.touching_live_tables() == []


def test_navigation_ids_are_metadata_only(client, issued_world):
    """Source ids are returned so a UI can link to them; no text or amount comes from following them."""
    document = read(client, issued_world)
    assert document["customer_id"] == str(issued_world.customer.id)
    assert document["lines"][0]["source_line_id"] == str(issued_world.line.id)
    # Every piece of document text is in the document itself.
    assert document["customer_snapshot"]["name"] == "Umeå HK" and document["issuer_snapshot"]["legal_name"] == "Solo AB"
    assert {f["key"] for f in document["lines"][0]["fields"]} == {"owner", "kind"}
    assert {f["display"] for f in document["lines"][0]["fields"]} == {"Anna Andersson", "Therapy"}


def test_reading_an_issued_invoice_never_recalculates_anything(client, issued_world, monkeypatch):
    """The stored amounts are shown as stored: even if every Sales calculation were broken, an
    issued invoice reads the same."""
    import app.modules.invoicing.service as invoicing_service
    import app.modules.sales.pricing as pricing

    def explode(*args, **kwargs):
        raise AssertionError("an issued invoice must not be recalculated")

    for name in ("calculate_line", "calculate_totals"):
        monkeypatch.setattr(pricing, name, explode)
    monkeypatch.setattr(invoicing_service, "calculate_totals", explode)
    monkeypatch.setattr(invoicing_service, "sum_lines", explode)

    assert read(client, issued_world) == issued_world.document
    assert client.get(INVOICES, headers=issued_world.headers).status_code == 200
