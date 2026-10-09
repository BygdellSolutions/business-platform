"""Return cases on issued invoices: requested → goods received → approved (then credited) or rejected.

A case is the work, not the accounting: it says what the customer wants to send back, why, and when to look at it
again, and keeps a log of every step and note. Goods that come back can go into stock at "goods received" (Inventory's
"stock.return" hook, as for credit notes). An approved case is closed by the credit note made from it (see
`credits.create_credit_note`, which locks the case in the same database transaction). Every step locks the case row.
"""

import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import audit, clock
from app.core.entity_registry import registry
from app.core.tenant import TenantContext
from app.core.tenant_scope import get_scoped_or_404, reference_error, scoped_select
from app.models import User
from app.modules.invoicing.credits import credited_quantities
from app.modules.invoicing.models import (
    OPEN_RETURN_STATES,
    Invoice,
    InvoiceLine,
    InvoiceReturn,
    InvoiceReturnEvent,
    InvoiceReturnLine,
    InvoiceStatus,
    ReturnState,
)
from app.modules.invoicing.schemas import ReturnCreate, ReturnEventRead, ReturnLineRead, ReturnRead

NO_QUANTITY = Decimal("0.000")


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail={"code": code, "message": message})


def _event(db: Session, ctx: TenantContext, case: InvoiceReturn, kind: str, note: str | None = None) -> None:
    # Stamped from the application clock, so the log keeps the order of the steps even within one database transaction.
    db.add(InvoiceReturnEvent(organization_id=ctx.organization_id, return_id=case.id, kind=kind, note=note, created_by=ctx.user.id, created_at=clock.utcnow()))


def _audit(db: Session, ctx: TenantContext, case: InvoiceReturn, action: str, text: str) -> None:
    audit.record(db, ctx, entity_type="invoice", entity_id=case.invoice_id, action=action, changes={"return": {"from": None, "to": text[:500]}})


def open_return(db: Session, ctx: TenantContext, invoice_id: uuid.UUID, payload: ReturnCreate) -> uuid.UUID:
    invoice = get_scoped_or_404(db, ctx, Invoice, invoice_id, for_update=True)
    if invoice.status != InvoiceStatus.ISSUED:
        raise _conflict("invoice_not_issued", "Only an issued invoice can have a return.")
    lines = {line.id: line for line in db.scalars(scoped_select(InvoiceLine, ctx).where(InvoiceLine.invoice_id == invoice.id))}
    credited = credited_quantities(db, ctx.organization_id, list(lines))
    for index, requested in enumerate(payload.lines):
        line = lines.get(requested.invoice_line_id)
        if line is None:
            reference_error(("lines", index, "invoice_line_id"), "This line is not on the invoice", "return.line_not_on_invoice")
        left = line.quantity - credited.get(line.id, NO_QUANTITY)
        if requested.quantity > left:
            reference_error(("lines", index, "quantity"), f"Only {left.normalize():f} of this line is not credited yet", "return.too_much")
    case = InvoiceReturn(
        organization_id=ctx.organization_id,
        invoice_id=invoice.id,
        state=ReturnState.REQUESTED,
        reason=payload.reason,
        follow_up_on=payload.follow_up_on,
        created_by=ctx.user.id,
        created_at=clock.utcnow(),
    )
    db.add(case)
    db.flush()
    for requested in payload.lines:
        db.add(InvoiceReturnLine(organization_id=ctx.organization_id, return_id=case.id, invoice_line_id=requested.invoice_line_id, quantity=requested.quantity))
    _event(db, ctx, case, "opened", payload.reason)
    _audit(db, ctx, case, "return_opened", f"Return opened: {payload.reason}")
    db.commit()
    return case.id


def _locked(db: Session, ctx: TenantContext, invoice_id: uuid.UUID, return_id: uuid.UUID) -> InvoiceReturn:
    case = db.scalar(
        scoped_select(InvoiceReturn, ctx).where(InvoiceReturn.invoice_id == invoice_id, InvoiceReturn.id == return_id).with_for_update()
    )
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    return case


def _require(case: InvoiceReturn, *states: str, doing: str) -> None:
    if case.state not in states:
        raise _conflict("return_state", f"A return that is {case.state.replace('_', ' ')} cannot be {doing}.")


def goods_received(db: Session, ctx: TenantContext, invoice_id: uuid.UUID, return_id: uuid.UUID, to_stock: Sequence[uuid.UUID], note: str | None) -> None:
    """The goods came back. The lines in `to_stock` go back into stock (their whole returned quantity)."""
    case = _locked(db, ctx, invoice_id, return_id)
    _require(case, ReturnState.REQUESTED, doing="marked as received")
    rows = {row.invoice_line_id: row for row in db.scalars(scoped_select(InvoiceReturnLine, ctx).where(InvoiceReturnLine.return_id == case.id))}
    unknown = [line_id for line_id in to_stock if line_id not in rows]
    if unknown:
        reference_error(("to_stock",), "Only lines of this return can go back into stock", "return.line_not_on_return")
    invoice_lines = {
        line.id: line for line in db.scalars(scoped_select(InvoiceLine, ctx).where(InvoiceLine.id.in_([line_id for line_id in to_stock])))
    } if to_stock else {}
    returnable: dict[uuid.UUID, Decimal] = {}
    for answer in registry.call_hooks("stock.returnable", db, ctx.organization_id, [line.source_line_id for line in invoice_lines.values()]):
        returnable.update(answer)
    for line_id in to_stock:
        source = invoice_lines[line_id].source_line_id
        if source not in returnable:
            reference_error(("to_stock",), f"{invoice_lines[line_id].description}: this line has no stock to return", "stock.not_tracked")
        if rows[line_id].quantity > returnable[source]:
            reference_error(
                ("to_stock",),
                f"{invoice_lines[line_id].description}: only {returnable[source].normalize():f} was delivered and not yet returned",
                "stock.return_too_much",
            )
    for line_id in to_stock:
        registry.call_hooks(
            "stock.return",
            db,
            ctx,
            transaction_line_id=invoice_lines[line_id].source_line_id,
            quantity=rows[line_id].quantity,
            note=f"Return: {case.reason}"[:255],
        )
        rows[line_id].returned_to_stock = True
    case.state = ReturnState.GOODS_RECEIVED
    _event(db, ctx, case, "goods_received", note)
    _audit(db, ctx, case, "return_received", "Goods received" + (" and returned to stock" if to_stock else ""))
    db.commit()


def approve(db: Session, ctx: TenantContext, invoice_id: uuid.UUID, return_id: uuid.UUID, note: str | None) -> None:
    case = _locked(db, ctx, invoice_id, return_id)
    _require(case, ReturnState.REQUESTED, ReturnState.GOODS_RECEIVED, doing="approved")
    case.state = ReturnState.APPROVED
    _event(db, ctx, case, "approved", note)
    _audit(db, ctx, case, "return_approved", "Return approved: to be credited")
    db.commit()


def reject(db: Session, ctx: TenantContext, invoice_id: uuid.UUID, return_id: uuid.UUID, reason: str) -> None:
    case = _locked(db, ctx, invoice_id, return_id)
    _require(case, ReturnState.REQUESTED, ReturnState.GOODS_RECEIVED, ReturnState.APPROVED, doing="rejected")
    case.state = ReturnState.REJECTED
    case.rejection_reason = reason
    _event(db, ctx, case, "rejected", reason)
    _audit(db, ctx, case, "return_rejected", f"Return rejected: {reason}")
    db.commit()


def add_note(db: Session, ctx: TenantContext, invoice_id: uuid.UUID, return_id: uuid.UUID, note: str) -> None:
    case = _locked(db, ctx, invoice_id, return_id)
    _event(db, ctx, case, "note", note)
    db.commit()


def set_follow_up(db: Session, ctx: TenantContext, invoice_id: uuid.UUID, return_id: uuid.UUID, follow_up_on: date) -> None:
    case = _locked(db, ctx, invoice_id, return_id)
    _require(case, *OPEN_RETURN_STATES, doing="followed up")
    if case.follow_up_on != follow_up_on:
        _event(db, ctx, case, "follow_up", f"Follow up on {follow_up_on.isoformat()} (was {case.follow_up_on.isoformat()})")
        case.follow_up_on = follow_up_on
    db.commit()


def close_with_credit(db: Session, ctx: TenantContext, invoice_id: uuid.UUID, return_id: uuid.UUID, credit_note_id: uuid.UUID, number_text: str) -> None:
    """Called by credit note creation, inside its database transaction (no commit here)."""
    case = _locked(db, ctx, invoice_id, return_id)
    _require(case, ReturnState.APPROVED, doing="credited")
    case.state = ReturnState.CREDITED
    case.credit_note_id = credit_note_id
    _event(db, ctx, case, "credited", f"Credit note {number_text}")


def lock_approved(db: Session, ctx: TenantContext, invoice_id: uuid.UUID, return_id: uuid.UUID) -> InvoiceReturn:
    """Checked before a credit note is written for the case: it must be approved."""
    case = _locked(db, ctx, invoice_id, return_id)
    _require(case, ReturnState.APPROVED, doing="credited")
    return case


# --- reading -----------------------------------------------------------------------------------------------------------


def list_returns(db: Session, ctx: TenantContext, invoice_id: uuid.UUID) -> list[ReturnRead]:
    cases = list(db.scalars(scoped_select(InvoiceReturn, ctx).where(InvoiceReturn.invoice_id == invoice_id).order_by(InvoiceReturn.created_at, InvoiceReturn.id)))
    if not cases:
        return []
    ids = [case.id for case in cases]
    lines = list(
        db.execute(
            select(InvoiceReturnLine, InvoiceLine.description, InvoiceLine.unit)
            .join(InvoiceLine, (InvoiceLine.organization_id == InvoiceReturnLine.organization_id) & (InvoiceLine.id == InvoiceReturnLine.invoice_line_id))
            .where(InvoiceReturnLine.organization_id == ctx.organization_id, InvoiceReturnLine.return_id.in_(ids))
            .order_by(InvoiceLine.position)
        )
    )
    events = list(
        db.execute(
            select(InvoiceReturnEvent, User.name)
            .outerjoin(User, User.id == InvoiceReturnEvent.created_by)
            .where(InvoiceReturnEvent.organization_id == ctx.organization_id, InvoiceReturnEvent.return_id.in_(ids))
            .order_by(InvoiceReturnEvent.created_at, InvoiceReturnEvent.id)
        )
    )
    return [
        ReturnRead(
            id=case.id,
            state=case.state,
            reason=case.reason,
            follow_up_on=case.follow_up_on,
            rejection_reason=case.rejection_reason,
            credit_note_id=case.credit_note_id,
            created_at=case.created_at,
            lines=[
                ReturnLineRead(invoice_line_id=row.invoice_line_id, description=description, unit=unit, quantity=row.quantity, returned_to_stock=row.returned_to_stock)
                for row, description, unit in lines
                if row.return_id == case.id
            ],
            events=[
                ReturnEventRead(kind=event.kind, note=event.note, created_at=event.created_at, created_by_name=name) for event, name in events if event.return_id == case.id
            ],
        )
        for case in cases
    ]


def open_counts(db: Session, organization_id: uuid.UUID, invoice_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not invoice_ids:
        return {}
    rows = db.execute(
        select(InvoiceReturn.invoice_id, func.count())
        .where(InvoiceReturn.organization_id == organization_id, InvoiceReturn.invoice_id.in_(list(invoice_ids)), InvoiceReturn.state.in_(OPEN_RETURN_STATES))
        .group_by(InvoiceReturn.invoice_id)
    )
    return {invoice_id: count for invoice_id, count in rows}
