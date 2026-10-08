"""The stock ledger's rules, for this module's API and its lifecycle effects (deliveries, backorders, returns).

Every change of an item's stock goes through `record_movement` while the caller holds the item's
row lock (`lock_items`), so the "before" of a movement is always the latest "after".
"""

import uuid
from collections.abc import Iterable
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.tenant import TenantContext
from app.models import Item, ItemType
from app.modules.inventory.models import LineFulfillment, MovementReason, StockMovement
from app.modules.sales.models import TransactionLine

ZERO = Decimal("0.000")


def lock_items(db: Session, ctx: TenantContext, item_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, Item]:
    """Lock the items of the active organization, always in id order (two writers never wait on each other in a
    cycle). Ids of another organization or that do not exist are simply absent from the result."""
    ids = sorted(set(item_ids))
    if not ids:
        return {}
    rows = db.scalars(
        select(Item).where(Item.organization_id == ctx.organization_id, Item.id.in_(ids)).order_by(Item.id).with_for_update()
    )
    return {item.id: item for item in rows}


def on_hand(db: Session, organization_id: uuid.UUID, item_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, Decimal]:
    """The physical stock of each item: its latest movement's "after" (zero without movements)."""
    ids = list(set(item_ids))
    result = {item_id: ZERO for item_id in ids}
    if not ids:
        return result
    last = (
        select(func.max(StockMovement.sequence))
        .where(StockMovement.organization_id == organization_id, StockMovement.item_id.in_(ids))
        .group_by(StockMovement.item_id)
    )
    latest = select(StockMovement.item_id, StockMovement.quantity_after).where(StockMovement.sequence.in_(last))
    for item_id, quantity in db.execute(latest):
        result[item_id] = quantity
    return result


def committed(db: Session, organization_id: uuid.UUID, item_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, Decimal]:
    """Units already promised per item: what open backorders still wait for."""
    ids = list(set(item_ids))
    result = {item_id: ZERO for item_id in ids}
    if not ids:
        return result
    rows = db.execute(
        select(LineFulfillment.item_id, func.sum(LineFulfillment.backordered - LineFulfillment.fulfilled_later))
        .where(
            LineFulfillment.organization_id == organization_id,
            LineFulfillment.item_id.in_(ids),
            LineFulfillment.cancelled_at.is_(None),
            LineFulfillment.fulfilled_later < LineFulfillment.backordered,
        )
        .group_by(LineFulfillment.item_id)
    )
    for item_id, quantity in rows:
        result[item_id] = quantity
    return result


def available(db: Session, organization_id: uuid.UUID, item_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, tuple[Decimal, Decimal]]:
    """(on hand, available) per item. Nothing is set aside for a draft; units promised to open backorders are not
    available to anyone else (they go to the oldest waiting sale first, I5)."""
    ids = list(set(item_ids))
    stock = on_hand(db, organization_id, ids)
    promised = committed(db, organization_id, ids)
    return {item_id: (stock[item_id], max(ZERO, stock[item_id] - promised[item_id])) for item_id in ids}


def tracked_ids(db: Session, organization_id: uuid.UUID, item_ids: Iterable[uuid.UUID]) -> list[uuid.UUID]:
    """The given items that are this organization's products tracking stock (anything else is simply left out)."""
    ids = list(set(item_ids))
    if not ids:
        return []
    return list(
        db.scalars(
            select(Item.id).where(
                Item.organization_id == organization_id, Item.id.in_(ids), Item.type == ItemType.PRODUCT, Item.track_stock.is_(True)
            )
        )
    )


def has_movements(db: Session, organization_id: uuid.UUID, item_id: uuid.UUID) -> bool:
    return db.scalar(
        select(StockMovement.id).where(StockMovement.organization_id == organization_id, StockMovement.item_id == item_id).limit(1)
    ) is not None


def ensure_tracked(item: Item) -> None:
    if item.type != ItemType.PRODUCT or not item.track_stock:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[{"loc": ["body"], "msg": "This item does not track stock; turn on stock tracking for the product first", "type": "stock.not_tracked"}],
        )


def record_movement(
    db: Session,
    ctx: TenantContext,
    item: Item,
    change: Decimal,
    reason: MovementReason,
    *,
    note: str | None = None,
    transaction_id: uuid.UUID | None = None,
    transaction_line_id: uuid.UUID | None = None,
) -> StockMovement:
    """Append one movement. The caller holds `item`'s row lock; stock never goes below zero (422 `stock.negative`)."""
    before = on_hand(db, ctx.organization_id, [item.id])[item.id]
    after = before + change
    if after < 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[{"loc": ["body", "quantity"], "msg": f"Stock cannot go below zero (on hand: {before.normalize():f})", "type": "stock.negative"}],
        )
    movement = StockMovement(
        organization_id=ctx.organization_id,
        item_id=item.id,
        quantity_change=change,
        quantity_before=before,
        quantity_after=after,
        reason=reason,
        note=note,
        transaction_id=transaction_id,
        transaction_line_id=transaction_line_id,
        created_by=ctx.user.id,
    )
    db.add(movement)
    db.flush()
    return movement


# --- lifecycle effects: completion delivers what is available, reopen and cancel give it back -----------------------


def deliver_on_completion(db: Session, ctx: TenantContext, transaction_id: uuid.UUID) -> None:
    """Deliver what is available of every stock-tracking line, in line order, and backorder the shortage.

    Runs inside the completion's database transaction (the transaction row is locked by Sales); the items are
    locked here in id order. A line that already has an active fulfillment is left alone (cannot happen through
    the lifecycle, which cancels them on reopen; the unique index is the backstop).
    """
    lines = list(
        db.scalars(
            select(TransactionLine)
            .where(
                TransactionLine.organization_id == ctx.organization_id,
                TransactionLine.transaction_id == transaction_id,
                TransactionLine.item_id.is_not(None),
            )
            .order_by(TransactionLine.position, TransactionLine.id)
        )
    )
    tracked = set(tracked_ids(db, ctx.organization_id, [line.item_id for line in lines]))
    lines = [line for line in lines if line.item_id in tracked]
    if not lines:
        return
    items = lock_items(db, ctx, tracked)
    left = {item_id: figures[1] for item_id, figures in available(db, ctx.organization_id, tracked).items()}
    for line in lines:
        deliver = min(line.quantity, left[line.item_id])
        if deliver > 0:
            record_movement(db, ctx, items[line.item_id], -deliver, MovementReason.DELIVERY, transaction_id=transaction_id, transaction_line_id=line.id)
            left[line.item_id] -= deliver
        db.add(
            LineFulfillment(
                organization_id=ctx.organization_id,
                transaction_id=transaction_id,
                transaction_line_id=line.id,
                item_id=line.item_id,
                ordered=line.quantity,
                delivered=deliver,
                backordered=line.quantity - deliver,
                fulfilled_later=ZERO,
                created_by=ctx.user.id,
            )
        )
    db.flush()


def return_on_undo(db: Session, ctx: TenantContext, transaction_id: uuid.UUID, reason: str) -> None:
    """Give back everything delivered for the transaction (at completion and later) through return movements, and
    cancel its fulfillments and open backorders. Nothing earlier is changed: the history shows delivery and return."""
    rows = list(
        db.scalars(
            select(LineFulfillment)
            .where(
                LineFulfillment.organization_id == ctx.organization_id,
                LineFulfillment.transaction_id == transaction_id,
                LineFulfillment.cancelled_at.is_(None),
            )
            .order_by(LineFulfillment.created_at, LineFulfillment.id)
            .with_for_update()
        )
    )
    if not rows:
        return
    items = lock_items(db, ctx, [row.item_id for row in rows])
    now = clock.utcnow()
    for row in rows:
        given = row.delivered + row.fulfilled_later
        if given > 0:
            record_movement(
                db,
                ctx,
                items[row.item_id],
                given,
                MovementReason.RETURN,
                note="Transaction reopened" if reason == "reopen" else "Transaction cancelled",
                transaction_id=transaction_id,
                transaction_line_id=row.transaction_line_id,
            )
        row.cancelled_at = now
        row.cancelled_by = ctx.user.id
        row.cancel_reason = reason
    db.flush()


def fulfillment_state(row: LineFulfillment, on_hand_now: Decimal) -> str:
    """Waiting for stock / Partially fulfilled / Ready to fulfill / Fulfilled / Cancelled, as the backlog shows it."""
    if row.cancelled_at is not None:
        return "cancelled"
    if row.fulfilled_later >= row.backordered:
        return "fulfilled"
    if on_hand_now > 0:
        return "ready_to_fulfill"
    return "partially_fulfilled" if row.fulfilled_later > 0 else "waiting_for_stock"
