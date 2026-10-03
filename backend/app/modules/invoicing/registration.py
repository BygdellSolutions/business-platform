import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.entity_registry import Registry
from app.core.lifecycle import CANCEL, REOPEN, Problem
from app.core.tenant import TenantContext
from app.modules.invoicing.models import Invoice, InvoiceStatus, InvoiceTransaction

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
        message = f"This transaction is on an issued invoice and cannot be {action}"
        label = "Issued invoice"
    else:
        message = f"This transaction is reserved by a draft invoice and cannot be {action}; delete the draft invoice first"
        label = "Draft invoice"
    return [Problem(code="invoice.reserved", message=message, entity_type=TRANSACTION, entity_id=str(entity_id), label=label)]


def register(registry: Registry) -> None:
    """Add Invoicing's veto to the core lifecycle seam. Nothing else is exposed to other modules."""
    registry.add_validator(_reserved_transaction_validator)
