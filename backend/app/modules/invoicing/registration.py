import uuid

from fastapi import HTTPException
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.core.entity_registry import Registry
from app.core.lifecycle import CANCEL, REOPEN, Problem
from app.core.tenant import TenantContext
from app.modules.invoicing import service
from app.modules.invoicing.models import CreditNote, CreditNotePdf, Invoice, InvoicePdf, InvoiceStatus, InvoiceTransaction
from app.modules.invoicing.pdf import service as pdf_service

TRANSACTION = "transaction"


def _reserved_transaction_validator(
    db: Session, ctx: TenantContext, event: str, entity_key: str, entity_id: uuid.UUID
) -> list[Problem]:
    """A transaction that is on a draft OR an issued invoice cannot leave `completed`.

    Registered on the core lifecycle seam, so Sales never learns about invoices: it asks the seam
    before a reopen or a cancel, under its own row lock on the transaction. A draft's reservation
    ends by deleting the draft; an issued invoice's never ends in V1.
    """
    if event not in (REOPEN, CANCEL) or entity_key != TRANSACTION:
        return []
    invoice_status = db.scalar(
        select(Invoice.status)
        .join(
            InvoiceTransaction,
            (InvoiceTransaction.organization_id == Invoice.organization_id) & (InvoiceTransaction.invoice_id == Invoice.id),
        )
        .where(InvoiceTransaction.organization_id == ctx.organization_id, InvoiceTransaction.transaction_id == entity_id)
    )
    if invoice_status is None:
        return []
    action = "reopened" if event == REOPEN else "cancelled"
    if invoice_status == InvoiceStatus.ISSUED:
        message = f"This order is on an issued invoice and cannot be {action}"
        label = "Issued invoice"
    else:
        message = f"This order is reserved by a draft invoice and cannot be {action}; delete the draft invoice first"
        label = "Draft invoice"
    return [Problem(code="invoice.reserved", message=message, entity_type=TRANSACTION, entity_id=str(entity_id), label=label)]


def _after_delivery(db: Session, ctx: TenantContext, transaction_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    """Backordered units were delivered: invoice them by delivery (see service.invoice_deliveries)."""
    return service.invoice_deliveries(db, ctx, transaction_ids)


def _prepare_export(db: Session, ctx: TenantContext, limit: int) -> None:
    """Before a complete copy of the organization's data is taken: freeze the PDF of every issued invoice and every credit note that
    has none yet (a PDF is frozen on its first download, so one nobody opened has no stored bytes). At most `limit` documents per
    copy so the request stays short; the copy says how many are still without a PDF."""
    organization_id = ctx.organization_id
    invoices = select(Invoice.id).where(
        Invoice.organization_id == organization_id,
        Invoice.status == InvoiceStatus.ISSUED,
        ~exists().where(InvoicePdf.organization_id == Invoice.organization_id, InvoicePdf.invoice_id == Invoice.id),
    )
    notes = select(CreditNote.id).where(
        CreditNote.organization_id == organization_id,
        ~exists().where(CreditNotePdf.organization_id == CreditNote.organization_id, CreditNotePdf.credit_note_id == CreditNote.id),
    )
    for query, make in ((invoices.order_by(Invoice.number).limit(limit), pdf_service.get_or_create_pdf), (notes.order_by(CreditNote.number).limit(limit), pdf_service.get_or_create_credit_note_pdf)):
        for document_id in list(db.scalars(query)):
            try:
                make(db, ctx, document_id)
            except HTTPException:
                continue  # a document that cannot be rendered (for example unsupported characters) is reported as missing, not fatal


def register(registry: Registry) -> None:
    """Add Invoicing's veto to the core lifecycle seam, and invoice later deliveries (invoicing by delivery)."""
    registry.add_validator(_reserved_transaction_validator)
    registry.add_hook("stock.delivered_later", _after_delivery)
    registry.add_hook("organization.prepare_export", _prepare_export)
