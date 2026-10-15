"""An invoice in another currency states its VAT amount in SEK: the seller types the exchange rate on the draft, the SEK amount is
calculated once when the invoice is issued (rounded half up to öre) and frozen."""

from decimal import ROUND_HALF_UP, Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.invoicing_support import INVOICES, completed, draft_invoice, if_match, issue

EUR_LINES = [{"description": "Consultation", "quantity": "1", "unit_price_ex_vat": "850.00", "vat_rate": "25.00"}]  # VAT 212.50 EUR


def _issue_response(client: TestClient, headers, invoice: dict):
    return client.post(f"{INVOICES}/{invoice['id']}/issue", headers={**headers, **if_match(invoice["version"])})


def _eur_draft(client, db_session, sales, **extra):
    return draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing, lines=EUR_LINES, currency="EUR"), **extra)


def test_an_invoice_in_euro_with_vat_cannot_be_issued_without_a_rate(client: TestClient, db_session: Session, sales):
    invoice = _eur_draft(client, db_session, sales)
    assert invoice["currency"] == "EUR" and invoice["vat_exchange_rate"] is None and invoice["vat_amount_sek"] is None
    refused = _issue_response(client, sales.headers, invoice)
    assert refused.status_code == 422 and "vat_exchange_rate" in refused.text
    assert client.get(f"{INVOICES}/{invoice['id']}", headers=sales.headers).json()["status"] == "draft"


def test_the_sek_amount_is_calculated_at_issue_rounded_half_up_and_frozen(client: TestClient, db_session: Session, sales):
    invoice = _eur_draft(client, db_session, sales, vat_exchange_rate="11.2345")
    assert invoice["vat_exchange_rate"] == "11.234500" and invoice["vat_amount_sek"] is None  # not yet: calculated at issue
    issued = issue(client, sales.headers, invoice)
    expected = (Decimal("212.50") * Decimal("11.2345")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    assert issued["vat_amount_sek"] == f"{expected:.2f}" == "2387.33"  # 2387.33125 -> 2387.33
    assert client.get(f"{INVOICES}/{invoice['id']}", headers=sales.headers).json()["vat_amount_sek"] == "2387.33"


def test_a_half_öre_rounds_up(client: TestClient, db_session: Session, sales):
    lines = [{"description": "Consultation", "quantity": "1", "unit_price_ex_vat": "800.00", "vat_rate": "25.00"}]  # VAT 200.00 EUR
    invoice = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing, lines=lines, currency="EUR"), vat_exchange_rate="10.000025")
    assert issue(client, sales.headers, invoice)["vat_amount_sek"] == "2000.01"  # 2000.005 exactly: half up, not to even


def test_a_rate_can_be_set_on_the_draft_afterwards(client: TestClient, db_session: Session, sales):
    invoice = _eur_draft(client, db_session, sales)
    patched = client.patch(f"{INVOICES}/{invoice['id']}", json={"vat_exchange_rate": "10"}, headers={**sales.headers, **if_match(invoice["version"])})
    assert patched.status_code == 200, patched.text
    issued = issue(client, sales.headers, client.get(f"{INVOICES}/{invoice['id']}", headers=sales.headers).json())
    assert issued["vat_amount_sek"] == "2125.00"


def test_a_malformed_rate_is_refused(client: TestClient, db_session: Session, sales):
    for bad in ("0", "-1", "abc", "1.1234567"):
        response = client.post(INVOICES, json={"transaction_ids": [str(completed(db_session, sales.org, sales.billing, currency="EUR").id)], "vat_exchange_rate": bad}, headers=sales.headers)
        assert response.status_code == 422, bad


def test_a_swedish_invoice_needs_no_rate_and_keeps_none(client: TestClient, db_session: Session, sales):
    invoice = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing), vat_exchange_rate="9.5")
    issued = issue(client, sales.headers, invoice)
    assert issued["currency"] == "SEK" and issued["vat_exchange_rate"] is None and issued["vat_amount_sek"] is None


def test_the_pdf_prints_the_sek_vat_and_the_rate(client: TestClient, db_session: Session, sales):
    from app.modules.invoicing.pdf.build import document_from_invoice
    from app.modules.invoicing.pdf.render import render_pdf
    from tests.pdf_support import pdf_text

    client.patch("/api/organization", json={"document_language": "sv"}, headers=sales.headers)
    issued = issue(client, sales.headers, _eur_draft(client, db_session, sales, vat_exchange_rate="11.2345"))
    text = pdf_text(render_pdf(document_from_invoice(issued)))
    assert "Momsbelopp i SEK" in text and "11,2345" in text and "2 387,33" in text
