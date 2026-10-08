"""The stock ledger's rules, for this module's API and (from slice I3) its lifecycle effects.

Every change of an item's stock goes through `record_movement` while the caller holds the item's
row lock (`lock_items`), so the "before" of a movement is always the latest "after".
"""

import uuid
from collections.abc import Iterable
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.tenant import TenantContext
from app.models import Item, ItemType
from app.modules.inventory.models import MovementReason, StockMovement

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


def available(db: Session, organization_id: uuid.UUID, item_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, tuple[Decimal, Decimal]]:
    """(on hand, available) per item. Nothing is set aside before completion, so today both are the physical stock;
    open backorders (I3) will be subtracted from "available" as units already promised."""
    stock = on_hand(db, organization_id, item_ids)
    return {item_id: (quantity, quantity) for item_id, quantity in stock.items()}


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
