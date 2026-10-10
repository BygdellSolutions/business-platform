"""Receipts of orders paid at the counter (2026-10-10): rendered with the invoice template ("Kvitto" / "Receipt").

The order itself is final (a paid order can never be reopened or cancelled), so its lines and amounts never change.
The receipt is rendered from it on each download, not stored: the seller's details printed are the organization's as
they are now. shortcut: not frozen like an invoice PDF; store the first rendering (as invoice_pdfs does) if a receipt
must be reproducible byte for byte or survive a change of the organization's profile.
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.org_time import today_in
from app.core.tenant import TenantContext
from app.core.tenant_scope import get_scoped, get_scoped_or_404, scoped_select
from app.models import Customer, Organization
from app.modules.invoicing import snapshots
from app.modules.invoicing.pdf import render
from app.modules.invoicing.pdf.build import document_from_invoice
from app.modules.invoicing.pdf.document import PdfReceipt
from app.modules.invoicing.pdf.labels import labels
from app.modules.sales.models import Transaction, TransactionLine
from app.modules.sales.pricing import calculate_totals


def receipt_pdf(db: Session, ctx: TenantContext, transaction_id: uuid.UUID) -> tuple[bytes, str]:
    """The receipt's PDF and its file name. Another organization's order, or a random id: 404."""
    order = get_scoped_or_404(db, ctx, Transaction, transaction_id)
    if order.paid_at is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail={"code": "not_paid_at_counter", "message": "Only an order paid at the counter has a receipt."}
        )
    lines = list(db.scalars(scoped_select(TransactionLine, ctx).where(TransactionLine.transaction_id == order.id).order_by(TransactionLine.position, TransactionLine.id)))
    customer = get_scoped(db, ctx, Customer, order.billing_customer_id)
    organization = db.scalar(select(Organization).where(Organization.id == ctx.organization_id))
    assert customer is not None and organization is not None  # the composite foreign keys guarantee both
    issuer = snapshots.issuer_snapshot(organization)
    totals = calculate_totals(lines)
    paid_on = today_in(organization.timezone, order.paid_at).isoformat()
    # The invoice document's JSON form, so the one template prints it (see pdf/build.py).
    data = {
        "status": "issued",
        "number_text": order.receipt_number_text,
        "issuer_snapshot": issuer,
        "customer_snapshot": snapshots.customer_snapshot(customer),
        "invoice_date": paid_on,
        "due_date": None,
        "currency": order.currency,
        "description": None,
        "transactions": [{"transaction_date": order.transaction_date.isoformat(), "transaction_number": order.number, "fields": []}],
        "lines": [
            {
                "position": line.position,
                "description": line.description,
                "unit": line.unit,
                "quantity": f"{line.quantity:.3f}",
                "unit_price_ex_vat": f"{line.unit_price_ex_vat:.2f}",
                "vat_rate": f"{line.vat_rate:.2f}",
                "net_amount": f"{line.net_amount:.2f}",
                "vat_amount": f"{line.vat_amount:.2f}",
                "gross_amount": f"{line.gross_amount:.2f}",
                "list_unit_price": f"{line.list_unit_price:.2f}" if line.list_unit_price is not None else None,
                "catalog_discount_percent": f"{line.catalog_discount_percent:.2f}" if line.catalog_discount_percent is not None else None,
                "customer_discount_percent": f"{line.customer_discount_percent:.2f}" if line.customer_discount_percent is not None else None,
                "line_discount_percent": f"{line.line_discount_percent:.2f}" if line.line_discount_percent is not None else None,
                "fields": [],
            }
            for line in lines
        ],
        "vat_breakdown": [{"vat_rate": f"{row.vat_rate:.2f}", "net_amount": f"{row.net_amount:.2f}", "vat_amount": f"{row.vat_amount:.2f}"} for row in totals.vat_breakdown],
        "net_amount": f"{totals.net_amount:.2f}",
        "vat_amount": f"{totals.vat_amount:.2f}",
        "gross_amount": f"{totals.gross_amount:.2f}",
    }
    document = document_from_invoice(data)
    words = labels(document.language)
    document = document.model_copy(update={"receipt": PdfReceipt(paid_on=paid_on, method=words[f"method_{order.payment_method}"]), "payment": None})
    return render.render_pdf(document), f"receipt-{order.receipt_number_text}.pdf"
