"""The life of an invoice: edit a draft, issue it, delete it, and what that does to Sales.

Rules under test:
  * a draft's header is editable with If-Match (428 / 400 / stale 409, no-op moves nothing);
  * issuing numbers and freezes it, re-takes the historical snapshots, and refuses to issue
    anything that no longer matches what the draft reserved;
  * numbers are per organization and series, handed out only at issuance;
  * a reserved transaction cannot be reopened or cancelled, through the core lifecycle seam alone;
  * a failure at any point leaves no partial record and no consumed number.
"""

import uuid

import pytest
from sqlalchemy import func, select, text

from app.models import Role
from app.modules.invoicing import numbering, service
from app.modules.invoicing.models import Invoice, InvoiceCounter, InvoiceLine, InvoiceTransaction, InvoiceVatRow
from app.modules.sales.models import Transaction, TransactionLine
from tests.factories import make_definition, make_org, make_value
from tests.invoicing_support import INVOICES, MUTATORS, Two, completed, draft_invoice, if_match, issue, member_of


def url(invoice: dict, action: str = "") -> str:
    return f"{INVOICES}/{invoice['id']}{action}"


def patch(client, headers, invoice, version, **body):
    return client.patch(url(invoice), json=body, headers={**headers, **if_match(version)})


def row_count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


# --- editing a draft ----------------------------------------------------------------------------------------------------


def test_a_draft_header_can_be_edited_and_each_real_change_moves_the_version(client, db_session, sales):
    invoice = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing), invoice_date="2026-10-01")

    first = patch(client, sales.headers, invoice, 1, description="October", due_date="2026-10-31")
    assert first.status_code == 200, first.text
    assert (first.json()["description"], first.json()["due_date"], first.json()["version"]) == ("October", "2026-10-31", 2)

    second = patch(client, sales.headers, invoice, 2, invoice_date="2026-10-02")
    assert (second.json()["invoice_date"], second.json()["version"]) == ("2026-10-02", 3)

    cleared = patch(client, sales.headers, invoice, 3, due_date=None, description="  ")
    assert (cleared.json()["due_date"], cleared.json()["description"], cleared.json()["version"]) == (None, None, 4)


def test_a_write_that_changes_nothing_does_not_move_the_version(client, db_session, sales):
    invoice = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing), invoice_date="2026-10-01", description="Same")

    same = patch(client, sales.headers, invoice, 1, invoice_date="2026-10-01", description="Same")
    empty = patch(client, sales.headers, invoice, 1)

    assert same.status_code == empty.status_code == 200
    assert same.json()["version"] == empty.json()["version"] == 1


def test_edits_need_a_precondition_and_a_stale_one_changes_nothing(raw_client, db_session, sales):
    invoice = draft_invoice(raw_client, sales.headers, completed(db_session, sales.org, sales.billing))

    missing = raw_client.patch(url(invoice), json={"description": "x"}, headers=sales.headers)
    malformed = raw_client.patch(url(invoice), json={"description": "x"}, headers={**sales.headers, "If-Match": "latest"})
    assert patch(raw_client, sales.headers, invoice, 1, description="first").status_code == 200
    stale = patch(raw_client, sales.headers, invoice, 1, description="second")

    assert (missing.status_code, malformed.status_code) == (428, 400)
    assert stale.status_code == 409
    assert stale.json()["detail"] == {
        "code": "stale_record",
        "message": "This record was changed by someone else since you loaded it; reload it and try again",
        "entity_type": "invoice",
        "entity_id": invoice["id"],
        "current_version": 2,
    }
    assert raw_client.get(url(invoice), headers=sales.headers).json()["description"] == "first"


def test_the_due_date_is_checked_against_the_final_state(client, db_session, sales):
    invoice = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing), invoice_date="2026-10-01", due_date="2026-10-10")

    later_invoice_date = patch(client, sales.headers, invoice, 1, invoice_date="2026-10-20")
    earlier_due_date = patch(client, sales.headers, invoice, 1, due_date="2026-09-01")
    moved_together = patch(client, sales.headers, invoice, 1, invoice_date="2026-10-20", due_date="2026-10-30")

    assert later_invoice_date.status_code == earlier_due_date.status_code == 422
    assert moved_together.status_code == 200 and moved_together.json()["version"] == 2


@pytest.mark.parametrize(
    "body",
    [{"invoice_date": None}, {"customer_id": str(uuid.uuid4())}, {"currency": "EUR"}, {"number": 5}, {"status": "issued"},
     {"transaction_ids": []}, {"net_amount": "1.00"}, {"organization_id": str(uuid.uuid4())}, {"description": "x" * 2001}],
)
def test_only_the_approved_header_fields_are_editable(client, db_session, sales, body):
    invoice = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing))
    assert patch(client, sales.headers, invoice, 1, **body).status_code == 422
    assert client.get(url(invoice), headers=sales.headers).json()["version"] == 1


@pytest.mark.parametrize("role", list(Role))
def test_only_owner_admin_and_accountant_edit_issue_and_delete(client, db_session, role):
    org = make_org(db_session)
    owner = member_of(db_session, org, Role.OWNER)
    who = member_of(db_session, org, role)
    allowed = role in MUTATORS
    a, b, c = (draft_invoice(client, owner, completed(db_session, org)) for _ in range(3))

    edit = client.patch(url(a), json={"description": "x"}, headers={**who, **if_match(1)})
    issue_response = client.post(url(b, "/issue"), headers={**who, **if_match(1)})
    delete = client.delete(url(c), headers={**who, **if_match(1)})

    assert [r.status_code for r in (edit, issue_response, delete)] == ([200, 200, 204] if allowed else [403, 403, 403])
    states = {row["id"]: row["status"] for row in client.get(INVOICES, headers=owner).json()}
    assert (states.get(b["id"]), c["id"] in states) == (("issued", False) if allowed else ("draft", True))


# --- foreign and random ids -------------------------------------------------------------------------------------------------


def test_foreign_and_random_invoice_ids_are_the_same_404_for_every_verb(client, db_session):
    two = Two(db_session)
    theirs = draft_invoice(client, two.b, two.b_tx)
    random = {"id": str(uuid.uuid4())}
    foreign = {"id": theirs["id"]}

    def calls(invoice):
        return [
            client.get(url(invoice), headers=two.a),
            client.patch(url(invoice), json={"description": "x"}, headers={**two.a, **if_match(1)}),
            client.patch(url(invoice), json={"description": "x"}, headers=two.a),  # no If-Match at all
            client.post(url(invoice, "/issue"), headers={**two.a, **if_match(1)}),
            client.post(url(invoice, "/issue"), headers={**two.a, **if_match(99)}),
            client.delete(url(invoice), headers={**two.a, **if_match(1)}),
            client.delete(url(invoice), headers=two.a),
        ]

    for mine, other in zip(calls(foreign), calls(random)):
        assert mine.status_code == other.status_code == 404
        assert mine.json() == other.json()
    assert client.get(url(theirs), headers=two.b).json()["status"] == "draft"  # untouched


def test_the_order_of_checks_is_found_then_state_then_version(raw_client, db_session, sales):
    invoice = draft_invoice(raw_client, sales.headers, completed(db_session, sales.org, sales.billing))
    issue(raw_client, sales.headers, invoice)
    for response in (
        patch(raw_client, sales.headers, invoice, 99, description="x"),  # stale AND issued: the state answers first
        raw_client.post(url(invoice, "/issue"), headers={**sales.headers, **if_match(99)}),
        raw_client.delete(url(invoice), headers={**sales.headers, **if_match(99)}),
    ):
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "invoice_issued"


# --- issuing ------------------------------------------------------------------------------------------------------------------


def test_issuing_numbers_and_freezes_the_invoice(client, db_session, sales):
    invoice = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing), invoice_date="2026-10-01")

    issued = issue(client, sales.headers, invoice)

    assert (issued["status"], issued["number"], issued["number_text"], issued["version"]) == ("issued", 1, "1", 2)
    assert issued["issued_at"] is not None and issued["issued_by"] is not None
    assert issued["series"] == "default"
    for kept in ("net_amount", "vat_amount", "gross_amount", "customer_id", "currency", "lines", "vat_breakdown", "invoice_date"):
        assert issued[kept] == invoice[kept]


def test_numbers_follow_issuance_order_not_creation_order(client, db_session, sales):
    first, second, third = (draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing)) for _ in range(3))

    assert [issue(client, sales.headers, x)["number"] for x in (third, first, second)] == [1, 2, 3]
    assert [r["number_text"] for r in client.get(INVOICES, params={"status": "issued"}, headers=sales.headers).json()] != []


def test_drafts_have_no_number_and_deleting_one_consumes_nothing(client, db_session, sales):
    gone = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing))
    kept = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing))
    assert (gone["number"], kept["number"]) == (None, None)
    assert client.delete(url(gone), headers={**sales.headers, **if_match(1)}).status_code == 204

    assert issue(client, sales.headers, kept)["number"] == 1
    assert row_count(db_session, InvoiceCounter) == 1


def test_identical_organizations_each_start_at_one(client, db_session):
    two = Two(db_session)
    a = issue(client, two.a, draft_invoice(client, two.a, two.a_tx))
    b = issue(client, two.b, draft_invoice(client, two.b, two.b_tx))
    a2 = issue(client, two.a, draft_invoice(client, two.a, completed(db_session, two.a_org, two.a_customer)))

    assert (a["number"], b["number"], a2["number"]) == (1, 1, 2)
    counters = {c.organization_id: c.next_number for c in db_session.scalars(select(InvoiceCounter))}
    assert counters == {two.a_org.id: 3, two.b_org.id: 2}


def test_issuing_needs_a_precondition_and_a_stale_one_consumes_no_number(raw_client, db_session, sales):
    invoice = draft_invoice(raw_client, sales.headers, completed(db_session, sales.org, sales.billing))
    assert patch(raw_client, sales.headers, invoice, 1, description="edited").status_code == 200

    missing = raw_client.post(url(invoice, "/issue"), headers=sales.headers)
    malformed = raw_client.post(url(invoice, "/issue"), headers={**sales.headers, "If-Match": "x"})
    stale = raw_client.post(url(invoice, "/issue"), headers={**sales.headers, **if_match(1)})

    assert (missing.status_code, malformed.status_code, stale.status_code) == (428, 400, 409)
    assert stale.json()["detail"]["code"] == "stale_record"
    assert row_count(db_session, InvoiceCounter) == 0
    assert raw_client.post(url(invoice, "/issue"), headers={**sales.headers, **if_match(2)}).status_code == 200


def test_an_issued_invoice_cannot_be_issued_edited_or_deleted_again(client, db_session, sales):
    invoice = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing))
    issued = issue(client, sales.headers, invoice)

    again = client.post(url(invoice, "/issue"), headers={**sales.headers, **if_match(2)})
    edit = patch(client, sales.headers, invoice, 2, description="changed")
    delete = client.delete(url(invoice), headers={**sales.headers, **if_match(2)})

    assert [r.status_code for r in (again, edit, delete)] == [409, 409, 409]
    assert client.get(url(invoice), headers=sales.headers).json() == issued
    assert row_count(db_session, InvoiceCounter) == 1 and db_session.scalar(select(InvoiceCounter.next_number)) == 2


def test_issuing_retakes_the_customer_organization_and_custom_field_snapshots(client, db_session, sales):
    org = sales.org
    tx = completed(db_session, org, sales.billing)
    line = db_session.scalar(select(TransactionLine).where(TransactionLine.transaction_id == tx.id))
    field = make_definition(db_session, org, key="note", label="Note", show_on_invoice=True)
    make_value(db_session, org, field, line.id, value_text="at draft time")
    # Values of fields that are NOT flagged for invoices never get into the document, at either stage.
    internal = make_definition(db_session, org, key="internal", label="Internal", position=90)
    make_value(db_session, org, internal, line.id, value_text="internal line")
    internal_header = make_definition(db_session, org, entity_type="transaction", key="internal_ref", label="Internal ref")
    make_value(db_session, org, internal_header, tx.id, value_text="internal header")
    # A field flagged at draft time but un-flagged before issuance is left out of the issued invoice.
    dropped = make_definition(db_session, org, key="dropped", label="Dropped", position=80, show_on_invoice=True)
    make_value(db_session, org, dropped, line.id, value_text="was flagged")
    draft = draft_invoice(client, sales.headers, tx)
    assert [f["key"] for f in draft["lines"][0]["fields"]] == ["note", "dropped"]  # in field order
    assert draft["transactions"][0]["fields"] == []
    assert draft["lines"][0]["fields"][0]["value"] == "at draft time"
    assert draft["customer_snapshot"]["name"] == "Umeå HK"

    # Everything the draft copied changes before issuance ...
    sales.billing.name, sales.billing.city, sales.billing.vat_number = "Umeå Hästklubb", "Luleå", "SE9"
    org.legal_name, org.address_line1 = "Solo Renamed AB", "New Street 1"
    field.label = "Remark"
    dropped.show_on_invoice = False
    db_session.execute(text("update custom_field_values set value_text = 'at issue time' where entity_id = :i"), {"i": line.id})
    db_session.flush()

    issued = issue(client, sales.headers, draft)

    # ... and the issued document has the issue-time content, not the draft-time one.
    assert issued["customer_name"] == "Umeå Hästklubb"
    assert (issued["customer_snapshot"]["name"], issued["customer_snapshot"]["city"], issued["customer_snapshot"]["vat_number"]) == ("Umeå Hästklubb", "Luleå", "SE9")
    assert (issued["issuer_snapshot"]["legal_name"], issued["issuer_snapshot"]["address_line1"]) == ("Solo Renamed AB", "New Street 1")
    assert [f["key"] for f in issued["lines"][0]["fields"]] == ["note"]  # not "internal", not the un-flagged "dropped"
    assert issued["transactions"][0]["fields"] == []  # not the unflagged header field
    entry = issued["lines"][0]["fields"][0]
    assert (entry["label"], entry["value"], entry["display"]) == ("Remark", "at issue time", "at issue time")
    # The money is not re-read: it is still exactly what the draft reserved.
    assert issued["lines"] == [{**l, "fields": issued["lines"][i]["fields"]} for i, l in enumerate(draft["lines"])]


# --- the reservation blocks Sales through the lifecycle seam ---------------------------------------------------------------


@pytest.mark.parametrize("step", ["reopen", "cancel"])
def test_a_transaction_on_a_draft_invoice_cannot_be_reopened_or_cancelled(client, db_session, sales, step):
    tx = completed(db_session, sales.org, sales.billing)
    draft_invoice(client, sales.headers, tx)

    response = client.post(f"/api/transactions/{tx.id}/{step}", headers=sales.headers)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert (detail["code"], detail["event"], detail["total"]) == ("validation_failed", step, 1)
    problem = detail["problems"][0]
    assert (problem["code"], problem["entity_type"], problem["entity_id"], problem["label"]) == ("invoice.reserved", "transaction", str(tx.id), "Draft invoice")
    assert "draft invoice" in problem["message"]
    db_session.refresh(tx)
    assert (tx.status, tx.version) == ("completed", 1)


@pytest.mark.parametrize("step", ["reopen", "cancel"])
def test_a_transaction_on_an_issued_invoice_can_never_be_reopened_or_cancelled(client, db_session, sales, step):
    tx = completed(db_session, sales.org, sales.billing)
    issue(client, sales.headers, draft_invoice(client, sales.headers, tx))

    response = client.post(f"/api/transactions/{tx.id}/{step}", headers=sales.headers)

    assert response.status_code == 409
    problem = response.json()["detail"]["problems"][0]
    assert (problem["code"], problem["label"]) == ("invoice.reserved", "Issued invoice")
    db_session.refresh(tx)
    assert tx.status == "completed"


def test_deleting_the_draft_releases_the_reservation(client, db_session, sales):
    tx = completed(db_session, sales.org, sales.billing)
    invoice = draft_invoice(client, sales.headers, tx)
    assert client.post(f"/api/transactions/{tx.id}/reopen", headers=sales.headers).status_code == 409

    assert client.delete(url(invoice), headers={**sales.headers, **if_match(1)}).status_code == 204

    reopened = client.post(f"/api/transactions/{tx.id}/reopen", headers=sales.headers)
    assert reopened.status_code == 200 and reopened.json()["status"] == "draft"
    assert (row_count(db_session, Invoice), row_count(db_session, InvoiceTransaction), row_count(db_session, InvoiceLine), row_count(db_session, InvoiceVatRow)) == (0, 0, 0, 0)


def test_another_transaction_is_unaffected_by_a_reservation(client, db_session, sales):
    reserved, free = completed(db_session, sales.org, sales.billing), completed(db_session, sales.org, sales.billing)
    draft_invoice(client, sales.headers, reserved)
    assert client.post(f"/api/transactions/{free.id}/reopen", headers=sales.headers).status_code == 200
    assert client.post(f"/api/transactions/{free.id}/cancel", headers=sales.headers).status_code == 200


def test_the_sources_of_an_invoice_stay_frozen_in_sales(client, db_session, sales):
    tx = completed(db_session, sales.org, sales.billing)
    draft_invoice(client, sales.headers, tx)
    line = db_session.scalar(select(TransactionLine).where(TransactionLine.transaction_id == tx.id))

    assert client.patch(f"/api/transactions/{tx.id}/lines/{line.id}", json={"description": "x"}, headers=sales.headers).status_code == 409
    assert client.delete(f"/api/transactions/{tx.id}", headers=sales.headers).status_code == 409
    assert client.post(f"/api/transactions/{tx.id}/lines", json={"description": "x", "unit": "u", "quantity": "1", "unit_price_ex_vat": "1.00", "vat_rate": "25"}, headers=sales.headers).status_code == 409


def test_a_source_transaction_is_never_marked_by_invoicing(client, db_session, sales):
    tx = completed(db_session, sales.org, sales.billing)
    before = client.get(f"/api/transactions/{tx.id}", headers=sales.headers).json()
    issue(client, sales.headers, draft_invoice(client, sales.headers, tx))
    assert client.get(f"/api/transactions/{tx.id}", headers=sales.headers).json() == before  # not even its version moved
    assert "invoic" not in " ".join(c.name for c in Transaction.__table__.columns).lower()


# --- deletion of drafts -------------------------------------------------------------------------------------------------------


def test_deleting_a_draft_needs_a_precondition_and_removes_all_of_it(raw_client, db_session, sales):
    invoice = draft_invoice(raw_client, sales.headers, completed(db_session, sales.org, sales.billing, lines=[{}, {}]))
    assert patch(raw_client, sales.headers, invoice, 1, description="edit").status_code == 200

    missing = raw_client.delete(url(invoice), headers=sales.headers)
    malformed = raw_client.delete(url(invoice), headers={**sales.headers, "If-Match": "?"})
    stale = raw_client.delete(url(invoice), headers={**sales.headers, **if_match(1)})
    assert (missing.status_code, malformed.status_code, stale.status_code) == (428, 400, 409)
    assert row_count(db_session, Invoice) == 1

    assert raw_client.delete(url(invoice), headers={**sales.headers, **if_match(2)}).status_code == 204
    assert [row_count(db_session, m) for m in (Invoice, InvoiceTransaction, InvoiceLine, InvoiceVatRow)] == [0, 0, 0, 0]
    assert raw_client.get(url(invoice), headers=sales.headers).status_code == 404


def test_sales_lines_and_customers_stay_protected_while_referenced(client, db_session, sales):
    tx = completed(db_session, sales.org, sales.billing)
    draft_invoice(client, sales.headers, tx)
    assert client.delete(f"/api/customers/{sales.billing.id}", headers=sales.headers).status_code == 409
    for statement in (
        "delete from transaction_lines where transaction_id = :t",
        "delete from transactions where id = :t",
    ):
        with pytest.raises(Exception, match="violates foreign key"):
            with db_session.begin_nested():
                db_session.execute(text(statement), {"t": tx.id})


# --- failures leave nothing behind -----------------------------------------------------------------------------------------------


def test_a_failure_during_creation_leaves_no_partial_invoice(client, db_session, sales, monkeypatch):
    tx = completed(db_session, sales.org, sales.billing, lines=[{}, {}])
    real = service._insert_children

    def fail_after_inserting(*args, **kwargs):
        real(*args, **kwargs)  # the invoice, sources, lines and VAT rows are all written ...
        raise RuntimeError("boom")  # ... and then it fails

    monkeypatch.setattr(service, "_insert_children", fail_after_inserting)
    with pytest.raises(RuntimeError, match="boom"):
        client.post(INVOICES, json={"transaction_ids": [str(tx.id)]}, headers=sales.headers)
    monkeypatch.undo()

    assert [row_count(db_session, m) for m in (Invoice, InvoiceTransaction, InvoiceLine, InvoiceVatRow)] == [0, 0, 0, 0]
    assert client.post(INVOICES, json={"transaction_ids": [str(tx.id)]}, headers=sales.headers).status_code == 201  # not stuck reserved


def test_a_failure_after_the_number_was_allocated_rolls_the_allocation_back(client, db_session, sales, monkeypatch):
    invoice = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing))
    real = numbering.allocate_number

    def allocate_then_fail(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError("boom after allocating")

    monkeypatch.setattr(numbering, "allocate_number", allocate_then_fail)
    with pytest.raises(RuntimeError, match="boom after allocating"):
        client.post(url(invoice, "/issue"), headers={**sales.headers, **if_match(1)})
    monkeypatch.undo()

    assert row_count(db_session, InvoiceCounter) == 0  # the allocation did not survive
    stored = client.get(url(invoice), headers=sales.headers).json()
    assert (stored["status"], stored["number"], stored["version"]) == ("draft", None, 1)
    assert issue(client, sales.headers, invoice)["number"] == 1  # the next issuance gets the number


def test_a_lost_race_for_a_transaction_gets_the_ordinary_already_invoiced_answer(client, db_session, sales, monkeypatch):
    """The ordinary check sees nothing (as it would for a concurrent creator that has not committed
    yet); the unique key on the source link is the last guard and answers the same 409."""
    tx = completed(db_session, sales.org, sales.billing)
    draft_invoice(client, sales.headers, tx)
    ordinary = client.post(INVOICES, json={"transaction_ids": [str(tx.id)]}, headers=sales.headers)
    real = service._already_invoiced
    calls: list[int] = []

    def blind_once(*args, **kwargs):
        calls.append(1)
        return [] if len(calls) == 1 else real(*args, **kwargs)

    monkeypatch.setattr(service, "_already_invoiced", blind_once)
    raced = client.post(INVOICES, json={"transaction_ids": [str(tx.id)]}, headers=sales.headers)

    assert ordinary.status_code == raced.status_code == 409
    assert raced.json() == ordinary.json()
    assert row_count(db_session, Invoice) == 1  # the loser left nothing behind


# --- an invoice that no longer matches its sources is not issued ------------------------------------------------------------------


def _tamper_and_issue(client, db_session, sales, statement: str, **params):
    tx = completed(db_session, sales.org, sales.billing, lines=[{}, {"description": "Second"}])
    invoice = draft_invoice(client, sales.headers, tx)
    # Raw SQL, like a bug or a manual change would be. The immutability triggers are switched off
    # for this rolled-back test transaction only (PostgreSQL DDL is transactional).
    for table in ("invoices", "invoice_lines", "invoice_vat_rows", "invoice_transactions"):
        db_session.execute(text(f"alter table {table} disable trigger user"))
    db_session.execute(text("alter table transactions disable trigger user"))
    db_session.execute(text(statement), {"t": tx.id, "i": uuid.UUID(invoice["id"]), **params})
    response = client.post(url(invoice, "/issue"), headers={**sales.headers, **if_match(1)})
    return invoice, response


@pytest.mark.parametrize(
    "statement",
    [
        "update transactions set status = 'draft' where id = :t",
        "update transactions set version = version + 1 where id = :t",
        "update transaction_lines set description = 'Changed' where transaction_id = :t and position = 2",
        "update transaction_lines set quantity = 2, net_amount = 2 * unit_price_ex_vat, vat_amount = round(2 * unit_price_ex_vat * vat_rate / 100, 2), gross_amount = 2 * unit_price_ex_vat + round(2 * unit_price_ex_vat * vat_rate / 100, 2) where transaction_id = :t and position = 1",
        "insert into transaction_lines (organization_id, transaction_id, position, description, unit, quantity, unit_price_ex_vat, vat_rate, net_amount, vat_amount, gross_amount) select organization_id, id, 9, 'Sneaked in', 'u', 1, 1.00, 25.00, 1.00, 0.25, 1.25 from transactions where id = :t",
        # a copy changed consistently (so the CHECKs accept it): 1 x 1000.00 at 25 %
        "update invoice_lines set unit_price_ex_vat = 1000.00, net_amount = 1000.00, vat_amount = 250.00, gross_amount = 1250.00 where invoice_id = :i and position = 1",
        "update invoices set net_amount = net_amount + 1, gross_amount = gross_amount + 1 where id = :i",
        "update invoice_vat_rows set vat_amount = vat_amount + 1 where invoice_id = :i",
        "delete from invoice_vat_rows where invoice_id = :i",
    ],
)
def test_issuing_fails_rather_than_issue_different_financial_content(client, db_session, sales, statement):
    invoice, response = _tamper_and_issue(client, db_session, sales, statement)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "source_changed"
    assert row_count(db_session, InvoiceCounter) == 0  # no number was consumed
    status = db_session.scalar(select(Invoice.status).where(Invoice.id == uuid.UUID(invoice["id"])))
    assert status == "draft"
