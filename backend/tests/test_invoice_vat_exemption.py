"""An invoice that charges 0 % VAT must say why (a stated ground, printed on the invoice)."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.invoicing_support import INVOICES, completed, draft_invoice, if_match, issue

ZERO = [{"description": "Export of goods", "quantity": "1", "unit_price_ex_vat": "1000.00", "vat_rate": "0.00"}]
REASON = "Reverse charge: the buyer is liable for the VAT (omvänd skattskyldighet)"


def _issue_response(client: TestClient, headers, invoice: dict):
    return client.post(f"{INVOICES}/{invoice['id']}/issue", headers={**headers, **if_match(invoice["version"])})


def test_a_zero_percent_invoice_cannot_be_issued_without_its_ground(client: TestClient, db_session: Session, sales):
    invoice = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing, lines=ZERO))
    refused = _issue_response(client, sales.headers, invoice)
    assert refused.status_code == 422
    assert "vat_exemption_reason" in refused.text
    read = client.get(f"{INVOICES}/{invoice['id']}", headers=sales.headers).json()
    assert read["status"] == "draft" and read["number"] is None  # nothing was numbered


def test_with_the_ground_it_is_issued_and_the_ground_is_kept(client: TestClient, db_session: Session, sales):
    invoice = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing, lines=ZERO), vat_exemption_reason=f"  {REASON}  ")
    assert invoice["vat_exemption_reason"] == REASON  # trimmed
    issued = issue(client, sales.headers, invoice)
    assert issued["vat_exemption_reason"] == REASON and issued["number"]


def test_the_ground_can_be_added_to_the_draft_and_then_it_issues(client: TestClient, db_session: Session, sales):
    invoice = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing, lines=ZERO))
    assert _issue_response(client, sales.headers, invoice).status_code == 422
    patched = client.patch(f"{INVOICES}/{invoice['id']}", json={"vat_exemption_reason": REASON}, headers={**sales.headers, **if_match(invoice["version"])})
    assert patched.status_code == 200, patched.text
    assert issue(client, sales.headers, client.get(f"{INVOICES}/{invoice['id']}", headers=sales.headers).json())["status"] == "issued"
    blank = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing, lines=ZERO), vat_exemption_reason="   ")
    assert blank["vat_exemption_reason"] is None  # blank means none stated


def test_an_invoice_with_vat_needs_no_ground(client: TestClient, db_session: Session, sales):
    invoice = draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing))
    assert invoice["vat_exemption_reason"] is None
    assert issue(client, sales.headers, invoice)["status"] == "issued"


def test_the_ground_is_printed_on_the_pdf_in_the_documents_language(client: TestClient, db_session: Session, sales):
    from app.modules.invoicing.pdf.build import document_from_invoice
    from app.modules.invoicing.pdf.render import render_pdf
    from tests.pdf_support import pdf_text

    client.patch("/api/organization", json={"document_language": "sv"}, headers=sales.headers)
    issued = issue(client, sales.headers, draft_invoice(client, sales.headers, completed(db_session, sales.org, sales.billing, lines=ZERO), vat_exemption_reason=REASON))
    text = pdf_text(render_pdf(document_from_invoice(issued)))
    assert "Ingen moms debiteras eftersom" in text and "omvänd skattskyldighet" in text
