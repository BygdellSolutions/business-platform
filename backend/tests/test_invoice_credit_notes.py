"""Credit notes (kreditfakturor) on issued invoices.

A credit note credits quantities of the invoice's own lines at their own prices, never more of a line than was
invoiced and not credited yet, with a reason. It is numbered from the invoice's series at once and never changed.
What the customer owes, the payment state and the dashboard figures are after credits; paid beyond that is a refund
due. Goods that come back go into stock through Inventory's hook. Another organization's invoice is a 404.
"""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy import text as text_sql
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.org_time import today_in
from app.models import ItemType, Role
from tests.factories import make_item
from tests.invoicing_support import Two, completed, draft_invoice, issue, member_of


def _issued(client: TestClient, db_session: Session, sales, lines=None, **extra):
    """An issued invoice; by default one line of 850.00 + 25 % VAT = 1062.50."""
    return issue(client, sales.headers, draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing, lines=lines), **extra))


def _credit(client: TestClient, headers, invoice, *lines, reason="Returned goods"):
    body = {"reason": reason, "lines": [{"invoice_line_id": line_id, "quantity": quantity, **extra} for line_id, quantity, *rest in lines for extra in [rest[0] if rest else {}]]}
    return client.post(f"/api/invoices/{invoice['id']}/credit-notes", json=body, headers=headers)


def _read(client: TestClient, headers, invoice):
    return client.get(f"/api/invoices/{invoice['id']}", headers=headers).json()


def _pay(client: TestClient, headers, invoice, amount: str):
    body = {"amount": amount, "paid_on": str(today_in(None)), "method": "bankgiro"}
    return client.post(f"/api/invoices/{invoice['id']}/payments", json=body, headers=headers)


TEN_LINES = [{"description": "Liniment", "unit": "pcs", "quantity": "10", "unit_price_ex_vat": "100.00", "vat_rate": "25.00"}]


def test_a_part_of_a_line_is_credited_with_its_own_number_and_the_invoice_owes_less(client: TestClient, db_session: Session, sales):
    invoice = _issued(client, db_session, sales, lines=TEN_LINES)  # 10 x 100.00 + 25 % = 1250.00
    line = invoice["lines"][0]["id"]

    response = _credit(client, sales.headers, invoice, (line, "2"))

    assert response.status_code == 201, response.text
    note = response.json()
    assert (note["net_amount"], note["vat_amount"], note["gross_amount"]) == ("200.00", "50.00", "250.00")
    assert note["invoice_number_text"] == invoice["number_text"] and note["invoice_date"] == invoice["invoice_date"]
    assert int(note["number_text"]) == int(invoice["number_text"]) + 1  # the same series: never the same number
    assert note["reason"] == "Returned goods" and note["issued_by_name"]
    assert [(l["description"], l["quantity"], l["unit_price_ex_vat"], l["gross_amount"]) for l in note["lines"]] == [("Liniment", "2.000", "100.00", "250.00")]
    assert note["vat_breakdown"] == [{"vat_rate": "25.00", "net_amount": "200.00", "vat_amount": "50.00"}]
    after = _read(client, sales.headers, invoice)
    assert (after["credited_amount"], after["credit_status"], after["outstanding_amount"], after["payment_status"]) == ("250.00", "partly_credited", "1000.00", "unpaid")
    assert (after["lines"][0]["credited_quantity"], after["lines"][0]["creditable_quantity"]) == ("2.000", "8.000")
    assert [(c["number_text"], c["gross_amount"]) for c in after["credit_notes"]] == [(note["number_text"], "250.00")]
    assert client.get(f"/api/invoices/credit-notes/{note['id']}", headers=sales.headers).json() == note


def test_a_line_credited_in_parts_adds_up_exactly_and_never_beyond_what_was_invoiced(client: TestClient, db_session: Session, sales):
    lines = [{"description": "Oats", "unit": "kg", "quantity": "3", "unit_price_ex_vat": "0.35", "vat_rate": "12.00"}]  # 1.05 + 0.13 = 1.18
    invoice = _issued(client, db_session, sales, lines=lines)
    line = invoice["lines"][0]["id"]

    first = _credit(client, sales.headers, invoice, (line, "1.5")).json()
    too_much = _credit(client, sales.headers, invoice, (line, "1.501"))
    rest = _credit(client, sales.headers, invoice, (line, "1.5")).json()

    assert too_much.status_code == 422 and too_much.json()["detail"][0]["type"] == "credit.too_much"
    assert "Only 1.5 of this line" in too_much.json()["detail"][0]["msg"]
    assert Decimal(first["net_amount"]) + Decimal(rest["net_amount"]) == Decimal(invoice["net_amount"])
    assert Decimal(first["gross_amount"]) + Decimal(rest["gross_amount"]) == Decimal(invoice["gross_amount"])
    after = _read(client, sales.headers, invoice)
    assert (after["credit_status"], after["outstanding_amount"], after["payment_status"]) == ("credited", "0.00", "paid")
    assert _credit(client, sales.headers, invoice, (line, "0.001")).status_code == 422


def test_credit_all_of_a_paid_invoice_leaves_a_refund_due(client: TestClient, db_session: Session, sales):
    invoice = _issued(client, db_session, sales)
    _pay(client, sales.headers, invoice, "1062.50")

    _credit(client, sales.headers, invoice, (invoice["lines"][0]["id"], "1"))

    after = _read(client, sales.headers, invoice)
    assert (after["credit_status"], after["outstanding_amount"], after["refund_due_amount"]) == ("credited", "0.00", "1062.50")
    assert _pay(client, sales.headers, invoice, "1.00").json()["detail"][0]["type"] == "payment.overpaid"
    summary = client.get("/api/invoices/summary", headers=sales.headers).json()
    assert summary["refund_due"] == {"count": 1, "amounts": [{"currency": "SEK", "amount": "1062.50"}]}
    assert summary["issued_this_month"]["amounts"] in ([], [{"currency": "SEK", "amount": "0.00"}])  # worth nothing after the credit
    ids = {row["id"] for row in client.get("/api/invoices", params={"payment": "refund_due"}, headers=sales.headers).json()}
    assert ids == {invoice["id"]}


def test_a_partly_credited_invoice_counts_only_what_is_still_owed(client: TestClient, db_session: Session, sales):
    invoice = _issued(client, db_session, sales, lines=TEN_LINES)
    other = _issued(client, db_session, sales)
    _credit(client, sales.headers, invoice, (invoice["lines"][0]["id"], "4"))  # 1250.00 - 500.00
    _pay(client, sales.headers, invoice, "750.00")

    def ids(**params):
        return {row["id"] for row in client.get("/api/invoices", params=params, headers=sales.headers).json()}

    assert ids(payment="paid") == {invoice["id"]}
    assert ids(payment="open") == {other["id"]}
    assert ids(credit="any") == ids(credit="partly_credited") == {invoice["id"]}
    assert ids(credit="credited") == set()
    listed = next(row for row in client.get("/api/invoices", headers=sales.headers).json() if row["id"] == invoice["id"])
    assert (listed["credit_status"], listed["credited_amount"], listed["payment_status"]) == ("partly_credited", "500.00", "paid")
    summary = client.get("/api/invoices/summary", headers=sales.headers).json()
    assert summary["unpaid"] == {"count": 1, "amounts": [{"currency": "SEK", "amount": "1062.50"}]}


def test_what_a_credit_note_needs(client: TestClient, db_session: Session, sales):
    invoice = _issued(client, db_session, sales)
    line = invoice["lines"][0]["id"]
    other_invoice = _issued(client, db_session, sales)
    draft = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing))

    no_reason = _credit(client, sales.headers, invoice, (line, "1"), reason="  ")
    no_lines = client.post(f"/api/invoices/{invoice['id']}/credit-notes", json={"reason": "x", "lines": []}, headers=sales.headers)
    twice = _credit(client, sales.headers, invoice, (line, "0.5"), (line, "0.5"))
    foreign_line = _credit(client, sales.headers, invoice, (other_invoice["lines"][0]["id"], "1"))
    on_draft = _credit(client, sales.headers, draft, (draft["lines"][0]["id"], "1"))
    amounts = client.post(
        f"/api/invoices/{invoice['id']}/credit-notes",
        json={"reason": "x", "lines": [{"invoice_line_id": line, "quantity": "1", "net_amount": "1.00"}]},
        headers=sales.headers,
    )

    assert [r.status_code for r in (no_reason, no_lines, twice, amounts)] == [422, 422, 422, 422]
    assert foreign_line.status_code == 422 and foreign_line.json()["detail"][0]["type"] == "credit.line_not_on_invoice"
    assert on_draft.status_code == 409 and on_draft.json()["detail"]["code"] == "invoice_not_issued"
    assert _read(client, sales.headers, invoice)["credit_notes"] == []


def test_credit_notes_are_never_changed_or_deleted(client: TestClient, db_session: Session, sales):
    invoice = _issued(client, db_session, sales)
    _credit(client, sales.headers, invoice, (invoice["lines"][0]["id"], "1"))
    for statement in (
        "UPDATE credit_notes SET reason = 'x'",
        "DELETE FROM credit_notes",
        "UPDATE credit_note_lines SET quantity = 2",
        "DELETE FROM credit_note_lines",
        "UPDATE credit_note_vat_rows SET net_amount = 1",
    ):
        with pytest.raises(DBAPIError, match="never changed or deleted"), db_session.begin_nested():
            db_session.execute(text(statement))


def test_crediting_is_recorded_in_the_invoice_history(client: TestClient, db_session: Session, sales):
    invoice = _issued(client, db_session, sales)
    note = _credit(client, sales.headers, invoice, (invoice["lines"][0]["id"], "1"), reason="Cancelled visit").json()
    events = client.get("/api/history", params={"entity_type": "invoice", "entity_id": invoice["id"]}, headers=sales.headers).json()["events"]
    credited = next(e for e in events if e["action"] == "credited")
    assert note["number_text"] in str(credited["changes"]) and "Cancelled visit" in str(credited["changes"])


@pytest.mark.parametrize("role,allowed", [(Role.ADMIN, True), (Role.ACCOUNTANT, True), (Role.EMPLOYEE, False), (Role.VIEWER, False)])
def test_who_may_credit(client: TestClient, db_session: Session, sales, role, allowed):
    invoice = _issued(client, db_session, sales)
    headers = member_of(db_session, sales.org, role)

    response = _credit(client, headers, invoice, (invoice["lines"][0]["id"], "1"))

    assert response.status_code == (201 if allowed else 403)
    assert len(_read(client, headers, invoice)["credit_notes"]) == (1 if allowed else 0)


def test_another_organizations_invoice_and_credit_notes_are_out_of_reach(client: TestClient, db_session: Session):
    two = Two(db_session)
    a_invoice = issue(client, two.a, draft_invoice(client, two.a, completed(db_session, two.a_org, two.a_customer)))
    b_invoice = issue(client, two.b, draft_invoice(client, two.b, completed(db_session, two.b_org, two.b_customer)))
    b_note = _credit(client, two.b, b_invoice, (b_invoice["lines"][0]["id"], "1")).json()

    assert _credit(client, two.a, b_invoice, (b_invoice["lines"][0]["id"], "1")).status_code == 404
    assert client.get(f"/api/invoices/credit-notes/{b_note['id']}", headers=two.a).status_code == 404
    # Org B's invoice line on Org A's invoice: not a line of this invoice.
    assert _credit(client, two.a, a_invoice, (b_invoice["lines"][0]["id"], "1")).json()["detail"][0]["type"] == "credit.line_not_on_invoice"
    assert _read(client, two.b, b_invoice)["credited_amount"] == b_note["gross_amount"]


# --- goods back into stock ----------------------------------------------------------------------------------------------


def _stocked_invoice(client: TestClient, db_session: Session, sales, quantity="3", on_hand="5"):
    product = make_item(db_session, sales.org, name="Liniment", type=ItemType.PRODUCT, unit="pcs", price_ex_vat="120.00", track_stock=True)
    count = {"kind": "count", "quantity": on_hand, "note": "Count"}
    assert client.post(f"/api/items/{product.id}/stock", json=count, headers=sales.headers).status_code == 201
    line = {"item": product, "description": "Liniment", "unit": "pcs", "quantity": quantity, "unit_price_ex_vat": "120.00", "vat_rate": "25.00"}
    tx = completed(db_session, sales.org, sales.billing, lines=[line], status="draft")
    assert client.post(f"/api/transactions/{tx.id}/complete", headers=sales.headers).status_code == 200
    return product, issue(client, sales.headers, draft_invoice(client, sales.headers, tx))


def _on_hand(client: TestClient, headers, product) -> str:
    return client.get(f"/api/items/{product.id}/stock", headers=headers).json()["on_hand"]


def test_returned_goods_go_back_into_stock_at_most_what_was_delivered(client: TestClient, db_session: Session, sales):
    product, invoice = _stocked_invoice(client, db_session, sales, quantity="3", on_hand="5")
    line = invoice["lines"][0]
    assert line["stock_returnable"] == "3.000" and _on_hand(client, sales.headers, product) == "2.000"

    _credit(client, sales.headers, invoice, (line["id"], "2", {"returned_to_stock": True}))
    kept = _credit(client, sales.headers, invoice, (line["id"], "0.5"))  # credited, not returned (damaged)
    too_many = _credit(client, sales.headers, invoice, (line["id"], "0.5", {"returned_to_stock": True}))

    assert kept.status_code == 201
    assert too_many.status_code == 201  # 1 of 3 delivered units is still out: 0.5 can come back
    assert _on_hand(client, sales.headers, product) == "4.500"
    after = _read(client, sales.headers, invoice)
    assert after["lines"][0]["stock_returnable"] == "0.500" and after["credit_status"] == "credited"
    movements = client.get(f"/api/items/{product.id}/stock", headers=sales.headers).json()["movements"]
    returns = [m for m in movements if m["reason"] == "return"]
    assert sorted(m["quantity_change"] for m in returns) == ["0.500", "2.000"] and all("Credit note" in m["note"] for m in returns)


def test_goods_never_delivered_cannot_go_back_into_stock(client: TestClient, db_session: Session, sales):
    product, invoice = _stocked_invoice(client, db_session, sales, quantity="3", on_hand="1")  # 1 delivered, 2 backordered
    line = invoice["lines"][0]

    response = _credit(client, sales.headers, invoice, (line["id"], "2", {"returned_to_stock": True}))

    assert response.status_code == 422 and response.json()["detail"][0]["type"] == "stock.return_too_much"
    assert _read(client, sales.headers, invoice)["credit_notes"] == []  # nothing is half done
    assert _on_hand(client, sales.headers, product) == "0.000"


def test_a_line_without_stock_tracking_offers_no_return_and_ignores_the_tick(client: TestClient, db_session: Session, sales):
    invoice = _issued(client, db_session, sales)
    line = invoice["lines"][0]
    assert line["stock_returnable"] is None
    note = _credit(client, sales.headers, invoice, (line["id"], "1", {"returned_to_stock": True}))
    assert note.status_code == 201 and note.json()["lines"][0]["returned_to_stock"] is False


# --- refunds: paying back what was paid beyond what is owed -----------------------------------------------------------


def _refund(client: TestClient, headers, invoice, amount: str, **extra):
    body = {"amount": amount, "paid_on": str(today_in(None)), "method": "bank_transfer", **extra}
    return client.post(f"/api/invoices/{invoice['id']}/refunds", json=body, headers=headers)


def test_a_refund_pays_back_at_most_what_is_due_and_settles_the_invoice(client: TestClient, db_session: Session, sales):
    invoice = _issued(client, db_session, sales, lines=TEN_LINES)  # 1250.00
    _pay(client, sales.headers, invoice, "1250.00")
    _credit(client, sales.headers, invoice, (invoice["lines"][0]["id"], "2"))  # 250.00 to pay back

    too_much = _refund(client, sales.headers, invoice, "250.01")
    response = _refund(client, sales.headers, invoice, "250.00", reference="Bank 77")

    assert too_much.status_code == 422 and too_much.json()["detail"][0]["type"] == "refund.too_much"
    assert "Only 250.00 SEK is to be paid back" in too_much.json()["detail"][0]["msg"]
    body = response.json()
    assert response.status_code == 201
    assert (body["paid_amount"], body["refund_due_amount"], body["outstanding_amount"], body["payment_status"]) == ("1000.00", "0.00", "0.00", "paid")
    assert [(p["kind"], p["amount"]) for p in body["payments"]] == [("payment", "1250.00"), ("refund", "-250.00")]
    assert _refund(client, sales.headers, invoice, "0.01").status_code == 422
    summary = client.get("/api/invoices/summary", headers=sales.headers).json()
    assert summary["refund_due"]["count"] == 0
    assert summary["paid_this_month"] == {"count": 1, "amounts": [{"currency": "SEK", "amount": "1000.00"}]}


def test_a_refund_recorded_by_mistake_is_reversed_and_its_payment_only_after_it(client: TestClient, db_session: Session, sales):
    invoice = _issued(client, db_session, sales)
    payment = _pay(client, sales.headers, invoice, "1062.50").json()["payments"][0]["id"]
    _credit(client, sales.headers, invoice, (invoice["lines"][0]["id"], "1"))
    refund = _refund(client, sales.headers, invoice, "1062.50").json()["payments"][1]["id"]

    blocked = client.post(f"/api/invoices/{invoice['id']}/payments/{payment}/reverse", json={}, headers=sales.headers)
    undone = client.post(f"/api/invoices/{invoice['id']}/payments/{refund}/reverse", json={"note": "Wrong account"}, headers=sales.headers)

    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "payment_refunded"
    body = undone.json()
    assert [(p["kind"], p["amount"], p["reversed"]) for p in body["payments"]] == [
        ("payment", "1062.50", False), ("refund", "-1062.50", True), ("reversal", "1062.50", False)
    ]
    assert body["refund_due_amount"] == "1062.50"


def test_a_refund_needs_money_paid_beyond_what_is_owed(client: TestClient, db_session: Session, sales):
    invoice = _issued(client, db_session, sales)
    _pay(client, sales.headers, invoice, "1062.50")
    response = _refund(client, sales.headers, invoice, "1.00")
    assert response.status_code == 422 and "Only 0.00 SEK" in response.json()["detail"][0]["msg"]
    viewer = member_of(db_session, sales.org, Role.EMPLOYEE)
    assert _refund(client, viewer, invoice, "1.00").status_code == 403
    two = Two(db_session)
    assert _refund(client, two.a, invoice, "1.00").status_code == 404


# --- the credit note PDF ----------------------------------------------------------------------------------------------


def test_the_credit_note_pdf_is_a_kreditfaktura_referring_to_the_invoice_with_negative_amounts(client: TestClient, db_session: Session, sales):
    from tests.pdf_support import pdf_text, squeezed

    invoice = _issued(client, db_session, sales, lines=TEN_LINES)
    note = _credit(client, sales.headers, invoice, (invoice["lines"][0]["id"], "2"), reason="Two bottles broken").json()
    url = f"/api/invoices/credit-notes/{note['id']}/pdf"

    first = client.get(url, headers=sales.headers)
    again = client.get(url, headers=sales.headers)

    assert first.status_code == 200 and first.headers["content-type"] == "application/pdf"
    assert first.headers["content-disposition"] == f'attachment; filename="credit-note-{note["number_text"]}.pdf"'
    assert again.content == first.content
    text = squeezed(pdf_text(first.content))
    for expected in ("Credit note", "Credits invoice", invoice["number_text"], "Two bottles broken", "-250.00", "-200.00", "-50.00", "Total credited"):
        assert squeezed(expected) in text, expected
    assert squeezed("Amount due") not in text and squeezed("Payment reference") not in text
    count = db_session.execute(text_sql("select count(*) from credit_note_pdfs where credit_note_id = :n"), {"n": note["id"]}).scalar()
    assert count == 1
    two = Two(db_session)
    assert client.get(url, headers=two.a).status_code == 404


def test_a_swedish_organizations_credit_note_says_kreditfaktura(client: TestClient, db_session: Session, sales):
    from tests.pdf_support import pdf_text, squeezed

    sales.org.document_language = "sv"
    db_session.flush()
    invoice = _issued(client, db_session, sales)
    note = _credit(client, sales.headers, invoice, (invoice["lines"][0]["id"], "1")).json()

    text = squeezed(pdf_text(client.get(f"/api/invoices/credit-notes/{note['id']}/pdf", headers=sales.headers).content))

    for expected in ("Kreditfaktura", "Krediterar faktura", "Orsak", "Krediterat belopp", "-1062,50"):
        assert squeezed(expected) in text, expected
