"""A credit note on an invoice in another currency states the credited VAT in SEK at the exchange rate of the invoice it credits."""

from decimal import ROUND_HALF_UP, Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.invoicing_support import completed, draft_invoice, issue

EUR_LINES = [{"description": "Consultation", "quantity": "10", "unit_price_ex_vat": "100.00", "vat_rate": "25.00"}]  # VAT 250.00 EUR


def _credit(client: TestClient, headers, invoice, quantity="2"):
    body = {"reason": "Returned", "lines": [{"invoice_line_id": invoice["lines"][0]["id"], "quantity": quantity}]}
    response = client.post(f"/api/invoices/{invoice['id']}/credit-notes", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _eur_invoice(client, db_session, sales, rate="11.2345"):
    return issue(client, sales.headers, draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing, lines=EUR_LINES, currency="EUR"), vat_exchange_rate=rate))


def test_the_credited_vat_is_stated_in_sek_at_the_invoices_rate_and_frozen(client: TestClient, db_session: Session, sales):
    invoice = _eur_invoice(client, db_session, sales)
    note = _credit(client, sales.headers, invoice, "2")  # 2 of 10: VAT 50.00 EUR
    expected = (Decimal("50.00") * Decimal("11.2345")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    assert note["vat_amount"] == "50.00" and note["vat_exchange_rate"] == "11.234500" and note["vat_amount_sek"] == f"{expected:.2f}" == "561.73"
    assert client.get(f"/api/invoices/credit-notes/{note['id']}", headers=sales.headers).json()["vat_amount_sek"] == "561.73"
    listed = client.get(f"/api/invoices/{invoice['id']}", headers=sales.headers).json()["credit_notes"][0]
    assert listed["vat_amount_sek"] == "561.73"


def test_each_credit_note_uses_the_rate_its_invoice_was_issued_with(client: TestClient, db_session: Session, sales):
    first = _eur_invoice(client, db_session, sales, rate="10")
    second = _eur_invoice(client, db_session, sales, rate="12")
    assert _credit(client, sales.headers, first)["vat_amount_sek"] == "500.00"  # 50.00 x 10
    assert _credit(client, sales.headers, second)["vat_amount_sek"] == "600.00"  # 50.00 x 12


def test_a_credit_note_on_a_swedish_invoice_states_no_sek_amount(client: TestClient, db_session: Session, sales):
    invoice = issue(client, sales.headers, draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing, lines=EUR_LINES)))  # the organization's own currency (SEK)
    note = _credit(client, sales.headers, invoice)
    assert invoice["currency"] == "SEK" and note["vat_exchange_rate"] is None and note["vat_amount_sek"] is None


def test_the_pdf_of_the_credit_note_prints_the_credited_vat_in_sek_as_a_credit(client: TestClient, db_session: Session, sales):
    from app.modules.invoicing.pdf.build import document_from_credit_note
    from app.modules.invoicing.pdf.render import render_pdf
    from tests.pdf_support import pdf_text

    client.patch("/api/organization", json={"document_language": "sv"}, headers=sales.headers)
    note = _credit(client, sales.headers, _eur_invoice(client, db_session, sales))
    text = pdf_text(render_pdf(document_from_credit_note(note)))
    assert "Momsbelopp i SEK" in text and "11,2345" in text and "-561,73" in text.replace("−", "-")
