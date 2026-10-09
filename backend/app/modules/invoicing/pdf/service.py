"""Getting an invoice's PDF: the frozen artifact of the CURRENT template, created on first download.

Flow (no database row lock and no open transaction is held while ReportLab renders):

    1. read the invoice in the caller's organization (foreign and random ids are the one 404) and
       require it to be issued;
    2. if its artifact exists, return those exact bytes at once;
    3. build the PDF document solely from the stored invoice (invoicing tables only);
    4. end the read transaction, then render, outside any database lock;
    5. try to INSERT the artifact (`ON CONFLICT DO NOTHING` on one-per-invoice);
    6. whichever request wins, EVERYONE then reads the stored row and returns ITS bytes: a request
       that lost the race discards its own rendering and serves the winner's.

Concurrent first downloads may therefore render twice; exactly one artifact becomes canonical, and
every caller ends up with that artifact's bytes. A stored artifact is never changed: when the template changes, the
next download renders a NEW artifact with the current template (same invoice content) and serves that one; the older
artifacts stay stored as history (owner decision 2026-10-08: the current template at all times).
"""

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.tenant import TenantContext
from app.core.tenant_scope import get_scoped_or_404
from app.modules.invoicing import credits
from app.modules.invoicing import service as invoices
from app.modules.invoicing.models import CreditNotePdf, Invoice, InvoicePdf, InvoiceStatus
from app.modules.invoicing.pdf import render
from app.modules.invoicing.pdf.build import document_from_credit_note, document_from_invoice
from app.modules.invoicing.pdf.document import DocumentTooLarge, source_sha256
from app.modules.invoicing.pdf.filename import safe_filename
from app.modules.invoicing.pdf.fonts import UnsupportedCharacters

logger = logging.getLogger("app.invoicing.pdf")
MAX_LISTED_CHARACTERS = 20


@dataclass(frozen=True)
class StoredPdf:
    content: bytes
    sha256: str
    filename: str


def _stored(db: Session, ctx: TenantContext, invoice_id: uuid.UUID) -> InvoicePdf | None:
    """The invoice's artifact made with the current template, if there is one."""
    return db.scalar(
        select(InvoicePdf).where(
            InvoicePdf.organization_id == ctx.organization_id,
            InvoicePdf.invoice_id == invoice_id,
            InvoicePdf.template_version == render.TEMPLATE_VERSION,
        )
    )


def _refusal(error: Exception) -> HTTPException:
    if isinstance(error, UnsupportedCharacters):
        listed = [{"character": f"U+{ord(char):04X}", "reason": why} for char, why in list(error.found.items())[:MAX_LISTED_CHARACTERS]]
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": error.code,
                "message": "This invoice contains characters the PDF renderer cannot draw correctly, so no PDF was created. "
                "This is a limitation of the renderer, not of the invoice.",
                "characters": listed,
                "total": len(error.found),
            },
        )
    assert isinstance(error, DocumentTooLarge)
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"code": error.code, "message": error.message})


def get_or_create_pdf(db: Session, ctx: TenantContext, invoice_id: uuid.UUID) -> StoredPdf:
    invoice = get_scoped_or_404(db, ctx, Invoice, invoice_id)  # 1
    if invoice.status != InvoiceStatus.ISSUED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "invoice_not_issued", "message": "Only an issued invoice has a PDF. Issue the invoice first."},
        )
    filename = safe_filename(invoice.number_text)

    existing = _stored(db, ctx, invoice.id)  # 2
    if existing is not None:
        return StoredPdf(existing.content, existing.sha256, filename)

    document = document_from_invoice(invoices.read_invoice(db, ctx, invoice.id))  # 3 (invoicing tables only)
    db.commit()  # 4: no transaction is open while rendering

    started = time.monotonic()
    try:
        content = render.render_pdf(document)
    except (UnsupportedCharacters, DocumentTooLarge) as error:
        raise _refusal(error) from error

    db.execute(  # 5
        insert(InvoicePdf)
        .values(
            organization_id=ctx.organization_id,
            invoice_id=invoice.id,
            content=content,
            byte_size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            renderer=render.renderer_identity(),
            template_version=render.TEMPLATE_VERSION,
            source_sha256=source_sha256(document),
        )
        .on_conflict_do_nothing(index_elements=[InvoicePdf.organization_id, InvoicePdf.invoice_id, InvoicePdf.template_version])
    )
    db.commit()

    winner = _stored(db, ctx, invoice.id)  # 6: the stored row is the canonical artifact, whoever inserted it
    assert winner is not None
    logger.info("invoice pdf stored: invoice=%s organization=%s bytes=%d seconds=%.2f", invoice.id, ctx.organization_id, winner.byte_size, time.monotonic() - started)
    return StoredPdf(winner.content, winner.sha256, filename)


# --- credit notes: the same flow, its own artifacts ----------------------------------------------------------------


def _stored_credit(db: Session, ctx: TenantContext, credit_note_id: uuid.UUID) -> CreditNotePdf | None:
    return db.scalar(
        select(CreditNotePdf).where(
            CreditNotePdf.organization_id == ctx.organization_id,
            CreditNotePdf.credit_note_id == credit_note_id,
            CreditNotePdf.template_version == render.TEMPLATE_VERSION,
        )
    )


def get_or_create_credit_note_pdf(db: Session, ctx: TenantContext, credit_note_id: uuid.UUID) -> StoredPdf:
    """Steps 1-6 above for a credit note (a credit note is issued from the start, so there is no state to check)."""
    note = credits.read_credit_note(db, ctx, credit_note_id)  # 1: a foreign or random id is the one 404
    filename = safe_filename(note.number_text, "credit-note")
    existing = _stored_credit(db, ctx, note.id)
    if existing is not None:
        return StoredPdf(existing.content, existing.sha256, filename)

    document = document_from_credit_note(note)
    db.commit()
    try:
        content = render.render_pdf(document)
    except (UnsupportedCharacters, DocumentTooLarge) as error:
        raise _refusal(error) from error

    db.execute(
        insert(CreditNotePdf)
        .values(
            organization_id=ctx.organization_id,
            credit_note_id=note.id,
            content=content,
            byte_size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            renderer=render.renderer_identity(),
            template_version=render.TEMPLATE_VERSION,
            source_sha256=source_sha256(document),
        )
        .on_conflict_do_nothing(index_elements=[CreditNotePdf.organization_id, CreditNotePdf.credit_note_id, CreditNotePdf.template_version])
    )
    db.commit()
    winner = _stored_credit(db, ctx, note.id)
    assert winner is not None
    return StoredPdf(winner.content, winner.sha256, filename)
