"""Which invoices have been emailed to the customer (an email that was sent, or that the mail service has taken and will deliver).

A tiny read of the invoice-email log, kept apart from the emailing code so that reading invoices needs no PDF or mail machinery. An attempt
that failed does not count: the customer did not get it.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.modules.invoicing.models import Invoice, InvoiceEmail


def emailed_invoice_ids(db: Session, organization_id: uuid.UUID, invoice_ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
    if not invoice_ids:
        return set()
    return set(
        db.scalars(
            select(InvoiceEmail.invoice_id)
            .where(InvoiceEmail.organization_id == organization_id, InvoiceEmail.invoice_id.in_(list(invoice_ids)), InvoiceEmail.status.in_(("sent", "queued")))
            .distinct()
        )
    )


def emailed_expression():
    """True for an invoice of the surrounding query that has been emailed (for filtering a list by it)."""
    return exists().where(InvoiceEmail.organization_id == Invoice.organization_id, InvoiceEmail.invoice_id == Invoice.id, InvoiceEmail.status.in_(("sent", "queued")))
