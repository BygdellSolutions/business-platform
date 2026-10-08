import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authz import record_writer
from app.core.db import get_db
from app.core.tenant import TenantContext, get_tenant_context
from app.core.tenant_scope import get_scoped_or_404, reference_error
from app.models import Item, User
from app.modules.inventory import service
from app.modules.inventory.models import MovementReason, StockMovement
from app.modules.inventory.schemas import StockAdjustment, StockMovementRead, StockRead

router = APIRouter(prefix="/api/items", tags=["inventory"])

MOVEMENTS_SHOWN = 200


@router.get("/{item_id}/stock", response_model=StockRead)
def read_stock(item_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)) -> StockRead:
    """An item's on-hand quantity and the movements that explain it (any member)."""
    item = get_scoped_or_404(db, ctx, Item, item_id)
    return _stock_read(db, ctx, item)


@router.post("/{item_id}/stock", response_model=StockRead, status_code=status.HTTP_201_CREATED)
def adjust_stock(
    item_id: uuid.UUID, payload: StockAdjustment, ctx: TenantContext = Depends(record_writer), db: Session = Depends(get_db)
) -> StockRead:
    """Record a count, or stock added or removed by hand. The item's row lock orders this with every other movement."""
    item = get_scoped_or_404(db, ctx, Item, item_id, for_update=True)
    service.ensure_tracked(item)
    current = service.on_hand(db, ctx.organization_id, [item.id])[item.id]
    opening = payload.kind == "count" and not service.has_movements(db, ctx.organization_id, item.id)
    if not opening and payload.note is None:
        reference_error("note", "Say why the stock changes (for example: counted, damaged, lost)", "stock.note_required")
    if payload.kind == "count":
        change = payload.quantity - current
    else:
        change = payload.quantity if payload.kind == "add" else -payload.quantity
    if change == Decimal(0):
        reference_error("quantity", "This is the quantity on hand already; nothing changes", "stock.unchanged")
    service.record_movement(db, ctx, item, change, MovementReason.OPENING if opening else MovementReason.ADJUSTMENT, note=payload.note)
    db.commit()
    return _stock_read(db, ctx, item)


def _stock_read(db: Session, ctx: TenantContext, item: Item) -> StockRead:
    rows = db.execute(
        select(StockMovement, User.name)
        .outerjoin(User, User.id == StockMovement.created_by)
        .where(StockMovement.organization_id == ctx.organization_id, StockMovement.item_id == item.id)
        .order_by(StockMovement.sequence.desc())
        .limit(MOVEMENTS_SHOWN)
    ).all()
    return StockRead(
        item_id=item.id,
        track_stock=item.track_stock,
        on_hand=service.on_hand(db, ctx.organization_id, [item.id])[item.id],
        movements=[StockMovementRead.model_validate(movement).model_copy(update={"created_by_name": name}) for movement, name in rows],
    )
