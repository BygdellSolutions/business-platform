"""Credit notes (kreditfakturor): cancelling all or part of an issued invoice, line by line.

Everything happens under the invoice's row lock, so two people crediting the same invoice at once can never credit a
line beyond what was invoiced. A credit note is numbered from the invoice's series when it is created and is never
changed afterwards. When the person says goods came back, the "stock.return" hook is called for those lines (Inventory
records a return movement if it tracks the item); Invoicing never imports Inventory.
"""

import uuid
from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import audit, clock
from app.core.entity_registry import registry
from app.core.org_time import organization_today
from app.core.tenant import TenantContext
from app.core.tenant_scope import get_scoped_or_404, reference_error, scoped_select
from app.models import Organization, User
from app.modules.invoicing import numbering, snapshots
from app.modules.invoicing.models import CreditNote, CreditNoteLine, CreditNoteVatRow, Invoice, InvoiceLine, InvoiceStatus
from app.modules.invoicing.schemas import CreditNoteCreate, CreditNoteLineRead, CreditNoteRead, CreditNoteSummary, VatRowRead
from app.modules.sales.pricing import calculate_line

ZERO = Decimal("0.00")
NO_QUANTITY = Decimal("0.000")


def credited_quantities(db: Session, organization_id: uuid.UUID, invoice_line_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Decimal]:
    """How much of each invoice line has been credited so far."""
    if not invoice_line_ids:
        return {}
    rows = db.execute(
        select(CreditNoteLine.invoice_line_id, func.sum(CreditNoteLine.quantity))
        .where(CreditNoteLine.organization_id == organization_id, CreditNoteLine.invoice_line_id.in_(list(invoice_line_ids)))
        .group_by(CreditNoteLine.invoice_line_id)
    )
    return {line_id: quantity for line_id, quantity in rows}


def credited_amounts(db: Session, organization_id: uuid.UUID, invoice_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Decimal]:
    """The gross amount credited per invoice."""
    if not invoice_ids:
        return {}
    rows = db.execute(
        select(CreditNote.invoice_id, func.sum(CreditNote.gross_amount))
        .where(CreditNote.organization_id == organization_id, CreditNote.invoice_id.in_(list(invoice_ids)))
        .group_by(CreditNote.invoice_id)
    )
    return {invoice_id: amount for invoice_id, amount in rows}


def credited_sum_expression():
    """The credited gross of the invoice in the surrounding query (for filters and summaries)."""
    return (
        select(func.coalesce(func.sum(CreditNote.gross_amount), 0))
        .where(CreditNote.organization_id == Invoice.organization_id, CreditNote.invoice_id == Invoice.id)
        .scalar_subquery()
    )


def _credited_so_far(db: Session, organization_id: uuid.UUID, invoice_line_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, tuple[Decimal, Decimal, Decimal]]:
    """(quantity, net, vat) credited per invoice line, for the exact remainder of a line's final credit."""
    rows = db.execute(
        select(CreditNoteLine.invoice_line_id, func.sum(CreditNoteLine.quantity), func.sum(CreditNoteLine.net_amount), func.sum(CreditNoteLine.vat_amount))
        .where(CreditNoteLine.organization_id == organization_id, CreditNoteLine.invoice_line_id.in_(list(invoice_line_ids)))
        .group_by(CreditNoteLine.invoice_line_id)
    )
    return {line_id: (quantity, net, vat) for line_id, quantity, net, vat in rows}


def create_credit_note(db: Session, ctx: TenantContext, invoice_id: uuid.UUID, payload: CreditNoteCreate) -> uuid.UUID:
    invoice = get_scoped_or_404(db, ctx, Invoice, invoice_id, for_update=True)
    if invoice.status != InvoiceStatus.ISSUED:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail={"code": "invoice_not_issued", "message": "Only an issued invoice can be credited. A draft is deleted instead."}
        )
    from app.modules.invoicing import returns  # returns reads credited quantities from here

    if payload.return_id is not None:
        returns.lock_approved(db, ctx, invoice.id, payload.return_id)
    lines = {line.id: line for line in db.scalars(scoped_select(InvoiceLine, ctx).where(InvoiceLine.invoice_id == invoice.id))}
    so_far = _credited_so_far(db, ctx.organization_id, list(lines))
    # Checked before anything is written: what Inventory says can still go back into stock per line (absent: no stock).
    returnable: dict[uuid.UUID, Decimal] = {}
    for answer in registry.call_hooks("stock.returnable", db, ctx.organization_id, [line.source_line_id for line in lines.values()]):
        returnable.update(answer)

    rows: list[CreditNoteLine] = []
    stock_returns: list[tuple[InvoiceLine, Decimal]] = []
    for index, requested in enumerate(payload.lines):
        line = lines.get(requested.invoice_line_id)
        if line is None:
            reference_error(("lines", index, "invoice_line_id"), "This line is not on the invoice", "credit.line_not_on_invoice")
        done_quantity, done_net, done_vat = so_far.get(line.id, (NO_QUANTITY, ZERO, ZERO))
        left = line.quantity - done_quantity
        if requested.quantity > left:
            reference_error(("lines", index, "quantity"), f"Only {left.normalize():f} of this line can still be credited", "credit.too_much")
        if requested.quantity == left:
            # The last of the line: exactly what is left of its amounts, so a line credited in parts adds up to it.
            net, vat = line.net_amount - done_net, line.vat_amount - done_vat
        else:
            amounts = calculate_line(requested.quantity, line.unit_price_ex_vat, line.vat_rate)
            net, vat = amounts.net, amounts.vat
        rows.append(
            CreditNoteLine(
                organization_id=ctx.organization_id,
                invoice_line_id=line.id,
                position=line.position,
                description=line.description,
                unit=line.unit,
                quantity=requested.quantity,
                unit_price_ex_vat=line.unit_price_ex_vat,
                vat_rate=line.vat_rate,
                net_amount=net,
                vat_amount=vat,
                gross_amount=net + vat,
                returned_to_stock=requested.returned_to_stock,
            )
        )
        # The tick means something only for a line with stock to return; on any other line it is ignored.
        returned = requested.returned_to_stock and line.source_line_id in returnable
        if returned:
            if requested.quantity > returnable[line.source_line_id]:
                left_in_stock = returnable[line.source_line_id]
                reference_error(
                    ("lines", index, "returned_to_stock"),
                    f"Only {left_in_stock.normalize():f} of this line was delivered and not yet returned, so no more can go back into stock",
                    "stock.return_too_much",
                )
            stock_returns.append((line, requested.quantity))
        rows[-1].returned_to_stock = returned

    net = sum((row.net_amount for row in rows), ZERO)
    vat = sum((row.vat_amount for row in rows), ZERO)
    if net <= 0:
        reference_error(("lines",), "Nothing to credit: the chosen quantities are worth nothing", "credit.nothing")
    by_rate: dict[Decimal, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    for row in rows:
        by_rate[row.vat_rate][0] += row.net_amount
        by_rate[row.vat_rate][1] += row.vat_amount

    organization = db.scalar(select(Organization).where(Organization.id == ctx.organization_id))
    number = numbering.allocate_number(db, ctx.organization_id, invoice.series)
    note = CreditNote(
        organization_id=ctx.organization_id,
        invoice_id=invoice.id,
        series=invoice.series,
        number=number,
        number_text=numbering.format_number(number),
        credit_date=organization_today(db, ctx.organization_id),
        reason=payload.reason,
        currency=invoice.currency,
        customer_snapshot=invoice.customer_snapshot,
        issuer_snapshot=snapshots.issuer_snapshot(organization, ctx.user.name),
        net_amount=net,
        vat_amount=vat,
        gross_amount=net + vat,
        issued_at=clock.utcnow(),
        issued_by=ctx.user.id,
    )
    db.add(note)
    db.flush()
    for row in sorted(rows, key=lambda row: row.position):
        row.credit_note_id = note.id
        db.add(row)
    for rate, (rate_net, rate_vat) in sorted(by_rate.items()):
        db.add(CreditNoteVatRow(organization_id=ctx.organization_id, credit_note_id=note.id, vat_rate=rate, net_amount=rate_net, vat_amount=rate_vat))
    db.flush()
    for line, quantity in stock_returns:
        registry.call_hooks(
            "stock.return",
            db,
            ctx,
            transaction_line_id=line.source_line_id,
            quantity=quantity,
            note=f"Credit note {note.number_text}: {payload.reason}"[:255],
        )
    if payload.return_id is not None:
        returns.close_with_credit(db, ctx, invoice.id, payload.return_id, note.id, note.number_text)
    audit.record(
        db,
        ctx,
        entity_type="invoice",
        entity_id=invoice.id,
        action="credited",
        changes={"credit_note": {"from": None, "to": f"{note.number_text}: {note.gross_amount:.2f} {note.currency}. {payload.reason}"}},
    )
    db.commit()
    return note.id


def _names(db: Session, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    return dict(db.execute(select(User.id, User.name).where(User.id.in_(user_ids))).all()) if user_ids else {}


def credit_note_summaries(db: Session, ctx: TenantContext, invoice_id: uuid.UUID) -> list[CreditNoteSummary]:
    notes = list(db.scalars(scoped_select(CreditNote, ctx).where(CreditNote.invoice_id == invoice_id).order_by(CreditNote.number)))
    names = _names(db, {note.issued_by for note in notes})
    return [CreditNoteSummary(**_summary(note, names.get(note.issued_by))) for note in notes]


def read_credit_note(db: Session, ctx: TenantContext, credit_note_id: uuid.UUID) -> CreditNoteRead:
    note = get_scoped_or_404(db, ctx, CreditNote, credit_note_id)
    invoice = get_scoped_or_404(db, ctx, Invoice, note.invoice_id)
    lines = db.scalars(scoped_select(CreditNoteLine, ctx).where(CreditNoteLine.credit_note_id == note.id).order_by(CreditNoteLine.position))
    vat_rows = db.scalars(scoped_select(CreditNoteVatRow, ctx).where(CreditNoteVatRow.credit_note_id == note.id).order_by(CreditNoteVatRow.vat_rate))
    return CreditNoteRead(
        **_summary(note, _names(db, {note.issued_by}).get(note.issued_by)),
        invoice_id=invoice.id,
        invoice_number_text=invoice.number_text,
        invoice_date=invoice.invoice_date,
        customer_snapshot=note.customer_snapshot,
        issuer_snapshot=note.issuer_snapshot,
        lines=[CreditNoteLineRead.model_validate(line) for line in lines],
        vat_breakdown=[VatRowRead.model_validate(row) for row in vat_rows],
    )


def _summary(note: CreditNote, issued_by_name: str | None) -> dict:
    return dict(
        id=note.id,
        number_text=note.number_text,
        credit_date=note.credit_date,
        reason=note.reason,
        currency=note.currency,
        net_amount=note.net_amount,
        vat_amount=note.vat_amount,
        gross_amount=note.gross_amount,
        issued_at=note.issued_at,
        issued_by_name=issued_by_name,
    )
