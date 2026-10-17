"""Orders made from a booking (an appointment), and brought up to date when the booking changes.

Scheduling describes what should be billed (`BookingOrder`, see app/core/booking_orders.py) and asks through the registry; this module
builds the draft order with the same rules as an order typed by hand (the catalog price, the discounts of the day and the customer, VAT,
the organization's currency) and marks every line it makes with where it came from. Nothing here commits: the caller commits the order
together with the booking that points at it.

Updating touches only the lines that came from the booking: lines for new combinations are added, lines for combinations that were taken
away are removed when nobody has edited them since (an edited line is kept, and counted), and anything a person added by hand stays.
"""

import uuid
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.booking_orders import BookingLine, BookingOrder
from app.core.currency import default_currency_for_new_record
from app.core.org_time import organization_zone
from app.core.tenant import TenantContext
from app.core.tenant_scope import create_scoped, resolve_reference
from app.models import Customer
from app.core.lifecycle import CANCEL as EVENT_CANCEL, ensure_valid, run_effects
from app.modules.sales.api import _lock, _new_line_values, _require_draft
from app.modules.sales.models import Transaction, TransactionLine, TransactionStatus
from app.modules.sales.schemas import LineCreate

ONE = Decimal("1")


def _line_request(order: BookingOrder, line: BookingLine) -> LineCreate:
    return LineCreate(
        kind="service",
        item_id=line.item_id,
        quantity=ONE,
        performed_at=order.performed_at,
        performed_by_user_id=order.performed_by_user_id,
        subject_type=line.subject_type,
        subject_id=line.subject_id,
    )


def create_from_booking(db: Session, ctx: TenantContext, order: BookingOrder) -> dict:
    """A DRAFT order for the booking's customer, dated the booking's day in the organization's time zone, with one service line per
    (service, subject). Returns its id and number."""
    resolve_reference(db, ctx, Customer, order.customer_id, "customer_id", label="Customer")
    day = order.performed_at.astimezone(organization_zone(db, ctx.organization_id)).date()
    values = [
        _new_line_values(db, ctx, _line_request(order, line), ("lines", index), customer_id=order.customer_id, on_date=day)
        for index, line in enumerate(order.lines)
    ]
    transaction = create_scoped(
        db,
        ctx,
        Transaction,
        billing_customer_id=order.customer_id,
        currency=default_currency_for_new_record(db, ctx.organization_id),
        transaction_date=day,
    )
    audit.created(db, ctx, transaction, "transaction")
    for position, line_values in enumerate(values, start=1):
        line = create_scoped(
            db, ctx, TransactionLine, transaction_id=transaction.id, position=position, source_type=order.source_type, source_id=order.source_id, **line_values
        )
        audit.created(db, ctx, line, "transaction_line", context=("transaction", transaction.id))
    db.flush()
    db.refresh(transaction)
    return {"id": transaction.id, "number": transaction.number}


def cancel_draft(db: Session, ctx: TenantContext, order_id: uuid.UUID) -> int:
    """Cancel a DRAFT order because its booking was cancelled, with the checks and effects of cancelling it by hand. Nothing is committed:
    the caller commits it together with the booking. Returns the order's number."""
    transaction = _lock(db, ctx, order_id)
    _require_draft(transaction, "cancelled with its booking")
    ensure_valid(db, ctx, EVENT_CANCEL, "transaction", transaction.id)
    before = audit.snapshot(transaction)
    transaction.status = TransactionStatus.CANCELLED
    transaction.version += 1
    audit.updated(db, ctx, transaction, "transaction", before, action="cancelled")
    db.flush()
    run_effects(db, ctx, EVENT_CANCEL, "transaction", transaction.id)
    return transaction.number


def sync_from_booking(db: Session, ctx: TenantContext, order_id: uuid.UUID, order: BookingOrder) -> dict:
    """Bring a DRAFT order up to date with its booking; returns how many lines were added, removed and kept (edited, so left alone)."""
    transaction = _lock(db, ctx, order_id)
    _require_draft(transaction, "updated from its booking")
    own = {
        (line.item_id, line.subject_type, line.subject_id): line
        for line in db.scalars(
            select(TransactionLine).where(
                TransactionLine.organization_id == ctx.organization_id,
                TransactionLine.transaction_id == transaction.id,
                TransactionLine.source_type == order.source_type,
                TransactionLine.source_id == order.source_id,
            )
        )
    }
    wanted = {(line.item_id, line.subject_type, line.subject_id): line for line in order.lines}
    day = transaction.transaction_date
    position = db.scalar(
        select(func.coalesce(func.max(TransactionLine.position), 0)).where(
            TransactionLine.organization_id == ctx.organization_id, TransactionLine.transaction_id == transaction.id
        )
    )
    added = removed = kept = 0
    for key, line in wanted.items():
        if key in own:
            continue
        values = _new_line_values(db, ctx, _line_request(order, line), customer_id=transaction.billing_customer_id, on_date=day)
        position += 1
        created = create_scoped(
            db, ctx, TransactionLine, transaction_id=transaction.id, position=position, source_type=order.source_type, source_id=order.source_id, **values
        )
        audit.created(db, ctx, created, "transaction_line", context=("transaction", transaction.id))
        added += 1
    for key, line in own.items():
        if key in wanted:
            continue
        if line.version != 1:  # someone changed it after it was made: their change is kept
            kept += 1
            continue
        audit.deleted(db, ctx, line, "transaction_line", context=("transaction", transaction.id))
        db.delete(line)
        removed += 1
    if added or removed:
        transaction.version += 1
        audit.stamp(transaction, ctx)
    db.flush()
    return {"order_id": transaction.id, "number": transaction.number, "added": added, "removed": removed, "kept": kept}


def statuses(db: Session, organization_id: uuid.UUID, ids) -> dict[uuid.UUID, str]:
    """The status of the orders of ONE organization among `ids` (the answer to the hook other modules ask, never their rows)."""
    rows = db.execute(select(Transaction.id, Transaction.status).where(Transaction.organization_id == organization_id, Transaction.id.in_(list(ids)))).all()
    return {row[0]: row[1].value if isinstance(row[1], TransactionStatus) else str(row[1]) for row in rows}
