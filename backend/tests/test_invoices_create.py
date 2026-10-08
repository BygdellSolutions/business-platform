"""Creating a draft invoice: what may be combined, what is copied, who may do it.

The draft reserves whole completed transactions. The customer, the currency and every amount
come from the locked sources, never from the caller.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models import CustomerType, Role
from app.modules.invoicing.models import Invoice, InvoiceLine, InvoiceTransaction, InvoiceVatRow
from app.modules.sales.models import Transaction, TransactionLine
from app.modules.sales.pricing import calculate_totals
from tests.factories import make_customer, make_definition, make_org, make_value
from tests.invoicing_support import INVOICES, MUTATORS, Two, completed, draft_invoice, member_of


def post(client, headers, ids, **extra):
    return client.post(INVOICES, json={"transaction_ids": [str(i) for i in ids], **extra}, headers=headers)


def counts(db: Session) -> tuple[int, int, int, int]:
    return tuple(  # type: ignore[return-value]
        db.scalar(select(func.count()).select_from(model)) for model in (Invoice, InvoiceTransaction, InvoiceLine, InvoiceVatRow)
    )


# --- what may share an invoice -----------------------------------------------------------------------------------------


def test_one_completed_transaction_becomes_a_draft_that_reserves_it(client, db_session, sales):
    tx = completed(db_session, sales.org, sales.billing)

    response = post(client, sales.headers, [tx.id])

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["status"], body["version"], body["number"], body["number_text"], body["issued_at"]) == ("draft", 1, None, None, None)
    assert (body["customer_id"], body["currency"], body["transaction_count"]) == (str(sales.billing.id), "SEK", 1)
    assert [row["transaction_id"] for row in body["transactions"]] == [str(tx.id)]
    assert body["series"] == "default"
    assert "organization_id" not in body


def test_several_transactions_of_one_customer_and_currency_combine(client, db_session, sales):
    # Ordered by transaction date (then creation time, then id), so the order is predictable.
    first = completed(db_session, sales.org, sales.billing, lines=[{"description": "A"}, {"description": "B"}], transaction_date=date(2026, 9, 30))
    second = completed(db_session, sales.org, sales.billing, lines=[{"description": "C"}], transaction_date=date(2026, 10, 1))

    body = draft_invoice(client, sales.headers, first, second)

    assert body["transaction_count"] == 2
    assert [line["description"] for line in body["lines"]] == ["A", "B", "C"]
    assert [line["position"] for line in body["lines"]] == [1, 2, 3]
    assert {line["source_transaction_id"] for line in body["lines"]} == {str(first.id), str(second.id)}


def test_different_customers_cannot_combine(client, db_session, sales):
    other = make_customer(db_session, sales.org, "Someone Else")
    a, b = completed(db_session, sales.org, sales.billing), completed(db_session, sales.org, other)

    response = post(client, sales.headers, [a.id, b.id])

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "mixed_customers"
    assert counts(db_session) == (0, 0, 0, 0)


def test_different_currencies_cannot_combine(client, db_session, sales):
    a = completed(db_session, sales.org, sales.billing, currency="SEK")
    b = completed(db_session, sales.org, sales.billing, currency="EUR")

    response = post(client, sales.headers, [a.id, b.id])

    assert response.status_code == 409 and response.json()["detail"]["code"] == "mixed_currencies"
    assert counts(db_session) == (0, 0, 0, 0)


def test_a_transaction_without_a_currency_cannot_be_invoiced(client, db_session, sales):
    old = completed(db_session, sales.org, sales.billing, currency=None)
    fine = completed(db_session, sales.org, sales.billing)

    alone = post(client, sales.headers, [old.id])
    mixed = post(client, sales.headers, [old.id, fine.id])

    for response in (alone, mixed):
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["code"] == "currency_missing" and detail["transaction_ids"] == [str(old.id)]
    assert counts(db_session) == (0, 0, 0, 0)


@pytest.mark.parametrize("status", ["draft", "cancelled"])
def test_only_completed_transactions_can_be_invoiced(client, db_session, sales, status):
    tx = completed(db_session, sales.org, sales.billing, status=status)

    response = post(client, sales.headers, [tx.id])

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "transactions_not_completed"
    assert response.json()["detail"]["transaction_ids"] == [str(tx.id)]
    assert counts(db_session) == (0, 0, 0, 0)


def test_a_transaction_cannot_be_invoiced_twice_even_across_a_draft_and_an_issued_invoice(client, db_session, sales):
    on_draft, on_issued = completed(db_session, sales.org, sales.billing), completed(db_session, sales.org, sales.billing)
    draft_invoice(client, sales.headers, on_draft)
    done = draft_invoice(client, sales.headers, on_issued)
    client.post(f"{INVOICES}/{done['id']}/issue", headers={**sales.headers, "If-Match": '"1"'})

    for tx in (on_draft, on_issued):
        response = post(client, sales.headers, [tx.id])
        assert response.status_code == 409
        assert response.json()["detail"] == {
            "code": "already_invoiced",
            "message": "A transaction is already on a draft or issued invoice",
            "transaction_ids": [str(tx.id)],
        }
    assert counts(db_session)[0] == 2  # nothing new was created


def test_a_refused_request_creates_nothing_even_when_some_transactions_are_fine(client, db_session, sales):
    fine = completed(db_session, sales.org, sales.billing)
    draft = completed(db_session, sales.org, sales.billing, status="draft")

    assert post(client, sales.headers, [fine.id, draft.id]).status_code == 409
    assert counts(db_session) == (0, 0, 0, 0)
    # ... and the good one is still free.
    assert post(client, sales.headers, [fine.id]).status_code == 201


# --- the request ------------------------------------------------------------------------------------------------------


def test_the_caller_cannot_choose_the_customer_currency_amounts_or_organization(client, db_session, sales):
    tx = completed(db_session, sales.org, sales.billing)
    for field, value in (
        ("customer_id", str(uuid.uuid4())), ("currency", "EUR"), ("organization_id", str(uuid.uuid4())),
        ("net_amount", "1.00"), ("status", "issued"), ("number", 7), ("series", "x"), ("lines", []),
    ):
        response = post(client, sales.headers, [tx.id], **{field: value})
        assert response.status_code == 422, field
    assert counts(db_session) == (0, 0, 0, 0)


@pytest.mark.parametrize("ids", [[], None, "abc", ["not-a-uuid"]])
def test_the_transaction_list_must_be_a_list_of_uuids(client, sales, ids):
    response = client.post(INVOICES, json={"transaction_ids": ids}, headers=sales.headers)
    assert response.status_code == 422


def test_duplicate_and_too_many_ids_are_refused(client, db_session, sales):
    tx = completed(db_session, sales.org, sales.billing)
    assert post(client, sales.headers, [tx.id, tx.id]).status_code == 422
    assert post(client, sales.headers, [uuid.uuid4() for _ in range(201)]).status_code == 422
    assert counts(db_session) == (0, 0, 0, 0)


def test_header_fields_default_and_validate(client, db_session, sales):
    tx, other = completed(db_session, sales.org, sales.billing), completed(db_session, sales.org, sales.billing)

    body = post(client, sales.headers, [tx.id], invoice_date="2026-10-05", due_date="2026-11-04", description="  October work  ").json()

    assert (body["invoice_date"], body["due_date"], body["description"]) == ("2026-10-05", "2026-11-04", "October work")
    defaulted = post(client, sales.headers, [other.id]).json()
    assert defaulted["invoice_date"] and defaulted["due_date"] is None and defaulted["description"] is None


def test_due_date_cannot_precede_the_invoice_date(client, db_session, sales):
    tx = completed(db_session, sales.org, sales.billing)
    assert post(client, sales.headers, [tx.id], invoice_date="2026-10-05", due_date="2026-10-04").status_code == 422
    assert post(client, sales.headers, [tx.id], due_date="2000-01-01").status_code == 422  # before today's default
    assert counts(db_session) == (0, 0, 0, 0)


def test_blank_description_means_none_and_it_is_length_limited(client, db_session, sales):
    tx, other = completed(db_session, sales.org, sales.billing), completed(db_session, sales.org, sales.billing)
    assert post(client, sales.headers, [tx.id], description="   ").json()["description"] is None
    assert post(client, sales.headers, [other.id], description="x" * 2001).status_code == 422


# --- who may create ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(Role))
def test_owner_admin_and_accountant_create_everyone_else_is_refused(client, db_session, role):
    org = make_org(db_session)
    tx = completed(db_session, org)
    headers = member_of(db_session, org, role)

    response = post(client, headers, [tx.id])

    assert response.status_code == (201 if role in MUTATORS else 403)
    assert counts(db_session)[0] == (1 if role in MUTATORS else 0)


# --- foreign and random ids -------------------------------------------------------------------------------------------------


def test_foreign_random_and_mixed_ids_get_one_indistinguishable_answer(client, db_session):
    two = Two(db_session)
    mine = completed(db_session, two.a_org, two.a_customer)

    foreign = post(client, two.a, [two.b_tx.id])
    random = post(client, two.a, [uuid.uuid4()])
    mixed_foreign = post(client, two.a, [mine.id, two.b_tx.id])
    mixed_random = post(client, two.a, [mine.id, uuid.uuid4()])

    answers = [r.json() for r in (foreign, random, mixed_foreign, mixed_random)]
    assert all(r.status_code == 422 for r in (foreign, random, mixed_foreign, mixed_random))
    assert all(a == answers[0] for a in answers)  # byte-for-byte the same body
    assert "transaction_ids" in str(answers[0]) and str(two.b_tx.id) not in str(answers[0])
    assert counts(db_session) == (0, 0, 0, 0)


def test_a_foreign_transaction_is_never_reserved_or_changed(client, db_session):
    two = Two(db_session)
    post(client, two.a, [two.b_tx.id])
    assert db_session.scalar(select(func.count()).select_from(InvoiceTransaction)) == 0
    assert db_session.scalar(select(Transaction.version).where(Transaction.id == two.b_tx.id)) == 1


def test_identical_looking_organizations_each_get_their_own_invoice_and_number(client, db_session):
    two = Two(db_session)

    a = draft_invoice(client, two.a, two.a_tx)
    b = draft_invoice(client, two.b, two.b_tx)

    assert a["id"] != b["id"]
    assert client.get(f"{INVOICES}/{b['id']}", headers=two.a).status_code == 404
    assert client.get(f"{INVOICES}/{a['id']}", headers=two.b).status_code == 404
    assert [row["id"] for row in client.get(INVOICES, headers=two.a).json()] == [a["id"]]
    assert [row["id"] for row in client.get(INVOICES, headers=two.b).json()] == [b["id"]]
    assert db_session.scalar(select(Invoice.organization_id).where(Invoice.id == uuid.UUID(a["id"]))) == two.a_org.id


# --- copying ------------------------------------------------------------------------------------------------------------------

AWKWARD_LINES = [
    {"description": "Massage", "quantity": "2.375", "unit_price_ex_vat": "4.35", "vat_rate": "25.00"},
    {"description": "Travel", "quantity": "1", "unit_price_ex_vat": "0.10", "vat_rate": "6.00"},
    {"description": "Feed", "quantity": "3", "unit_price_ex_vat": "8.20", "vat_rate": "12.00"},
    {"description": "More travel", "quantity": "7.001", "unit_price_ex_vat": "0.33", "vat_rate": "6.00"},
    {"description": "Large", "quantity": "1", "unit_price_ex_vat": "9999999999.99", "vat_rate": "25.00"},
    {"description": "Free", "quantity": "5", "unit_price_ex_vat": "0.00", "vat_rate": "0.00"},
]


def test_sales_amounts_are_copied_verbatim_and_the_totals_are_their_sums(client, db_session, sales):
    first = completed(db_session, sales.org, sales.billing, lines=AWKWARD_LINES[:3])
    second = completed(db_session, sales.org, sales.billing, lines=AWKWARD_LINES[3:])

    body = draft_invoice(client, sales.headers, first, second)

    stored = {str(row.id): row for tx in (first, second) for row in tx_lines(db_session, tx)}
    assert len(body["lines"]) == len(stored) == 6
    for line in body["lines"]:
        source = stored[line["source_line_id"]]
        assert (line["description"], line["unit"]) == (source.description, source.unit)
        for column in ("quantity", "unit_price_ex_vat", "vat_rate", "net_amount", "vat_amount", "gross_amount"):
            assert Decimal(line[column]) == getattr(source, column), column
    # Header = plain sum of the line amounts; no re-rounding of the aggregate.
    assert Decimal(body["net_amount"]) == sum(Decimal(l["net_amount"]) for l in body["lines"])
    assert Decimal(body["vat_amount"]) == sum(Decimal(l["vat_amount"]) for l in body["lines"])
    assert Decimal(body["gross_amount"]) == Decimal(body["net_amount"]) + Decimal(body["vat_amount"])


def tx_lines(db: Session, tx):
    return list(db.scalars(select(TransactionLine).where(TransactionLine.transaction_id == tx.id)))


def test_the_stored_vat_breakdown_equals_the_grouping_of_the_stored_lines_and_of_sales(client, db_session, sales):
    first = completed(db_session, sales.org, sales.billing, lines=AWKWARD_LINES[:3])
    second = completed(db_session, sales.org, sales.billing, lines=AWKWARD_LINES[3:])

    body = draft_invoice(client, sales.headers, first, second)

    grouped: dict[str, list[Decimal]] = {}
    for line in body["lines"]:
        row = grouped.setdefault(line["vat_rate"], [Decimal(0), Decimal(0)])
        row[0] += Decimal(line["net_amount"])
        row[1] += Decimal(line["vat_amount"])
    stored = {row["vat_rate"]: [Decimal(row["net_amount"]), Decimal(row["vat_amount"])] for row in body["vat_breakdown"]}
    assert stored == grouped
    assert [row["vat_rate"] for row in body["vat_breakdown"]] == sorted(grouped, key=Decimal)
    # Parity with Sales: the same data grouped by Sales gives the same breakdown.
    sales_totals = calculate_totals([l for tx in (first, second) for l in tx_lines(db_session, tx)])
    assert {f"{r.vat_rate:.2f}": [r.net_amount, r.vat_amount] for r in sales_totals.vat_breakdown} == stored
    assert (Decimal(body["net_amount"]), Decimal(body["vat_amount"]), Decimal(body["gross_amount"])) == (
        sales_totals.net_amount, sales_totals.vat_amount, sales_totals.gross_amount,
    )


def test_a_line_that_came_from_an_item_carries_no_item_reference(client, db_session, sales):
    tx = completed(db_session, sales.org, sales.billing, lines=[{"item": sales.item, "description": "Horse massage"}])
    body = draft_invoice(client, sales.headers, tx)
    assert set(body["lines"][0]) == {
        "id", "position", "source_transaction_id", "source_line_id", "description", "unit", "quantity",
        "unit_price_ex_vat", "list_unit_price", "catalog_discount_percent", "customer_discount_percent", "line_discount_percent",
        "vat_rate", "net_amount", "vat_amount", "gross_amount", "fields", "service",
    }


def test_a_hand_built_completed_transaction_without_lines_still_gives_consistent_totals(client, db_session, sales):
    # Sales refuses to complete an empty transaction; a hand-built one must still not break totals.
    tx = completed(db_session, sales.org, sales.billing, lines=[])
    body = draft_invoice(client, sales.headers, tx)
    assert body["lines"] == [] and body["vat_breakdown"] == []
    assert (body["net_amount"], body["vat_amount"], body["gross_amount"]) == ("0.00", "0.00", "0.00")


# --- snapshots ----------------------------------------------------------------------------------------------------------------


def test_party_snapshots_hold_only_fields_that_exist(client, db_session, sales):
    sales.org.legal_name, sales.org.city, sales.org.vat_number = "Solo AB", "Umeå", "SE556000000101"
    customer = make_customer(db_session, sales.org, "Umeå HK", CustomerType.COMPANY, "hk@example.test", "070-1", city="Umeå", country_code="SE", vat_number="SE1")
    tx = completed(db_session, sales.org, customer)

    body = draft_invoice(client, sales.headers, tx)

    assert body["customer_snapshot"] == {
        "schema": 1, "customer_id": str(customer.id), "customer_type": "company", "name": "Umeå HK",
        "email": "hk@example.test", "phone": "070-1", "address_line1": None, "address_line2": None,
        "postal_code": None, "city": "Umeå", "country_code": "SE", "registration_number": None, "vat_number": "SE1",
    }
    assert body["issuer_snapshot"] == {
        "schema": 3, "organization_id": str(sales.org.id), "name": "Solo", "legal_name": "Solo AB",
        "address_line1": None, "address_line2": None, "postal_code": None, "city": "Umeå",
        "country_code": None, "registration_number": None, "vat_number": "SE556000000101",
        "phone": None, "email": None, "website": None, "bankgiro": None, "plusgiro": None, "iban": None, "bic": None,
        "payment_terms_days": None, "approved_for_f_tax": None, "document_language": None,
        "our_reference": body["issuer_snapshot"]["our_reference"],
    }
    assert body["customer_name"] == "Umeå HK"


def test_custom_fields_flagged_for_invoices_are_copied_generically(client, db_session, sales):
    org = sales.org
    anna = make_customer(db_session, org, "Anna Andersson")
    tx = completed(db_session, org, sales.billing)
    line = tx_lines(db_session, tx)[0]
    flagged_ref = make_definition(db_session, org, key="owner", label="Owner", field_type="reference", reference_source="customer", position=20, show_on_invoice=True)
    flagged_text = make_definition(db_session, org, key="note", label="Note", position=10, show_on_invoice=True)
    internal = make_definition(db_session, org, key="internal", label="Internal", position=30)  # not flagged
    off = make_definition(db_session, org, key="old", label="Old", position=40, show_on_invoice=True, enabled=False)
    header_field = make_definition(db_session, org, entity_type="transaction", key="po", label="PO number", field_type="text", show_on_invoice=True)
    make_definition(db_session, org, key="unset", label="Unset", position=50, show_on_invoice=True)  # flagged but no value
    make_value(db_session, org, flagged_ref, line.id, value_reference_id=anna.id)
    make_value(db_session, org, flagged_text, line.id, value_text="Handle with care")
    make_value(db_session, org, off, line.id, value_text="disabled")
    make_value(db_session, org, header_field, tx.id, value_text="PO-17")
    make_value(db_session, org, internal, line.id, value_text="not for the invoice")  # has a value, but is not flagged
    internal_header = make_definition(db_session, org, entity_type="transaction", key="internal_ref", label="Internal ref", position=20)
    make_value(db_session, org, internal_header, tx.id, value_text="not for the invoice either")

    body = draft_invoice(client, sales.headers, tx)

    assert body["transactions"][0]["fields"] == [
        {"key": "po", "label": "PO number", "field_type": "text", "value": "PO-17", "display": "PO-17",
         "missing": False, "position": 10, "definition_id": str(header_field.id)}
    ]
    fields = body["lines"][0]["fields"]
    assert [f["key"] for f in fields] == ["note", "owner"]  # field order; no internal, disabled or unset ones
    assert fields[0] == {"key": "note", "label": "Note", "field_type": "text", "value": "Handle with care", "display": "Handle with care",
                         "missing": False, "position": 10, "definition_id": str(flagged_text.id)}
    assert fields[1] == {"key": "owner", "label": "Owner", "field_type": "reference", "value": str(anna.id), "display": "Anna Andersson",
                         "missing": False, "position": 20, "definition_id": str(flagged_ref.id)}


def test_a_missing_reference_target_is_recorded_as_missing(client, db_session, sales):
    org = sales.org
    gone = make_customer(db_session, org, "Gone")
    tx = completed(db_session, org, sales.billing)
    field = make_definition(db_session, org, key="owner", label="Owner", field_type="reference", reference_source="customer", show_on_invoice=True)
    make_value(db_session, org, field, tx_lines(db_session, tx)[0].id, value_reference_id=gone.id)
    db_session.execute(text("delete from customers where id = :i"), {"i": gone.id})  # raw delete: dangling by design

    entry = draft_invoice(client, sales.headers, tx)["lines"][0]["fields"][0]

    assert (entry["missing"], entry["display"], entry["value"]) == (True, None, str(gone.id))
    assert entry["label"] == "Owner"


def test_the_custom_field_snapshot_knows_no_domain(client, db_session, sales):
    """Whatever the field is about, it is copied as key/label/type/value/display: no horse, owner or stable logic."""
    import inspect

    import app.modules.invoicing.snapshots as snapshots

    source = inspect.getsource(snapshots).lower()
    for word in ("horse", "owner", "stable", "equine"):
        assert word not in source.replace("organization", "")


def test_customer_deletion_is_refused_while_an_invoice_refers_to_it(client, db_session, sales):
    tx = completed(db_session, sales.org, sales.billing)
    draft_invoice(client, sales.headers, tx)
    response = client.delete(f"/api/customers/{sales.billing.id}", headers=sales.headers)
    assert response.status_code == 409


# --- the derived views ------------------------------------------------------------------------------------------------------------


def test_invoiceable_transactions_lists_only_what_can_be_invoiced(client, db_session, sales):
    ok_one = completed(db_session, sales.org, sales.billing, transaction_date=date(2026, 10, 2))
    ok_two = completed(db_session, sales.org, sales.billing, transaction_date=date(2026, 10, 1))
    completed(db_session, sales.org, sales.billing, status="draft")
    completed(db_session, sales.org, sales.billing, status="cancelled")
    completed(db_session, sales.org, sales.billing, currency=None)
    taken = completed(db_session, sales.org, sales.billing)
    draft_invoice(client, sales.headers, taken)

    rows = client.get("/api/invoiceable-transactions", headers=sales.headers).json()

    assert [row["id"] for row in rows] == [str(ok_one.id), str(ok_two.id)]
    row = rows[0]
    assert (row["currency"], row["line_count"], row["billing_customer"]["name"]) == ("SEK", 1, "Umeå HK")
    assert row["totals"] == {"net_amount": "850.00", "vat_amount": "212.50", "gross_amount": "1062.50"}
    only_customer = client.get("/api/invoiceable-transactions", params={"customer_id": str(uuid.uuid4())}, headers=sales.headers).json()
    assert only_customer == []


def test_deleting_the_draft_makes_the_transaction_invoiceable_again(client, db_session, sales):
    tx = completed(db_session, sales.org, sales.billing)
    invoice = draft_invoice(client, sales.headers, tx)
    assert client.get("/api/invoiceable-transactions", headers=sales.headers).json() == []

    assert client.delete(f"{INVOICES}/{invoice['id']}", headers={**sales.headers, "If-Match": '"1"'}).status_code == 204

    assert [r["id"] for r in client.get("/api/invoiceable-transactions", headers=sales.headers).json()] == [str(tx.id)]
    assert post(client, sales.headers, [tx.id]).status_code == 201


def test_invoice_state_by_transaction_is_derived_from_invoicing_records_only(client, db_session, sales):
    free, drafted, issued = (completed(db_session, sales.org, sales.billing) for _ in range(3))
    draft = draft_invoice(client, sales.headers, drafted)
    done = draft_invoice(client, sales.headers, issued)
    client.post(f"{INVOICES}/{done['id']}/issue", headers={**sales.headers, "If-Match": '"1"'})
    unknown = uuid.uuid4()

    rows = client.get(f"{INVOICES}/by-transaction", params={"ids": ",".join(map(str, (free.id, drafted.id, issued.id, unknown)))}, headers=sales.headers).json()

    assert [(r["transaction_id"], r["state"], r["invoice_id"], r["number_text"]) for r in rows] == [
        (str(free.id), "none", None, None),
        (str(drafted.id), "draft", draft["id"], None),
        (str(issued.id), "invoiced", done["id"], "1"),
        (str(unknown), "none", None, None),
    ]
    # Sales' own responses carry no invoicing state at all.
    assert "invoice" not in str(client.get(f"/api/transactions/{drafted.id}", headers=sales.headers).json()).lower()


def test_invoice_state_does_not_reveal_other_organizations(client, db_session):
    two = Two(db_session)
    draft_invoice(client, two.b, two.b_tx)

    rows = client.get(f"{INVOICES}/by-transaction", params={"ids": str(two.b_tx.id)}, headers=two.a).json()

    assert rows == [{"transaction_id": str(two.b_tx.id), "state": "none", "invoice_id": None, "number_text": None}]


@pytest.mark.parametrize("ids", ["", "nope", ",".join(["a"] * 3), ",".join(str(uuid.uuid4()) for _ in range(201))])
def test_invoice_state_validates_its_ids(client, sales, ids):
    assert client.get(f"{INVOICES}/by-transaction", params={"ids": ids}, headers=sales.headers).status_code == 422


def test_every_member_can_read_invoices_and_lists(client, db_session):
    org = make_org(db_session)
    tx = completed(db_session, org)
    owner = member_of(db_session, org, Role.OWNER)
    invoice = draft_invoice(client, owner, tx)
    for role in Role:
        headers = member_of(db_session, org, role)
        assert client.get(f"{INVOICES}/{invoice['id']}", headers=headers).status_code == 200
        assert len(client.get(INVOICES, headers=headers).json()) == 1
        assert client.get("/api/invoiceable-transactions", headers=headers).status_code == 200


def test_the_list_filters(client, db_session, sales):
    other = make_customer(db_session, sales.org, "Zed Customer")
    a = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing), invoice_date="2026-10-01")
    b = draft_invoice(client, sales.headers, completed(db_session, sales.org, other), invoice_date="2026-10-20")
    issued = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing), invoice_date="2026-10-10")
    client.post(f"{INVOICES}/{issued['id']}/issue", headers={**sales.headers, "If-Match": '"1"'})

    def ids(**params):
        return [row["id"] for row in client.get(INVOICES, params=params, headers=sales.headers).json()]

    assert ids() == [b["id"], issued["id"], a["id"]]  # newest invoice date first
    assert ids(status="issued") == [issued["id"]]
    assert ids(status="draft") == [b["id"], a["id"]]
    assert ids(customer_id=str(other.id)) == [b["id"]]
    assert ids(date_from="2026-10-05", date_to="2026-10-15") == [issued["id"]]
    assert ids(q="zed") == [b["id"]]
    assert ids(q="1") == [issued["id"]]  # by invoice number
    assert ids(q="%") == []  # literal, not a wildcard
    assert len(ids(limit=1)) == 1 and ids(limit=1, offset=1) == [issued["id"]]


def test_the_invoiceable_list_shows_only_the_active_organizations_transactions(client, db_session):
    two = Two(db_session)  # each organization has one identical-looking completed transaction

    a = client.get("/api/invoiceable-transactions", headers=two.a).json()
    b = client.get("/api/invoiceable-transactions", headers=two.b).json()

    assert [row["id"] for row in a] == [str(two.a_tx.id)]
    assert [row["id"] for row in b] == [str(two.b_tx.id)]
    # A filter by the other organization's customer finds nothing, like a random id would.
    assert client.get("/api/invoiceable-transactions", params={"customer_id": str(two.b_customer.id)}, headers=two.a).json() == []
    # Reserving one organization's transaction leaves the other's list alone.
    draft_invoice(client, two.a, two.a_tx)
    assert client.get("/api/invoiceable-transactions", headers=two.a).json() == []
    assert len(client.get("/api/invoiceable-transactions", headers=two.b).json()) == 1
