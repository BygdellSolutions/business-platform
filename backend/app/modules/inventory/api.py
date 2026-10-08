import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.authz import record_writer
from app.core.db import get_db
from app.core.tenant import TenantContext, get_tenant_context
from app.core.tenant_scope import get_scoped_or_404, reference_error
from app.models import Item, User
from app.modules.inventory import service
from app.modules.inventory.models import LineFulfillment, MovementReason, StockMovement
from app.modules.inventory.schemas import (
    ItemAvailability,
    LineFulfillmentRead,
    StockAdjustment,
    StockMovementRead,
    StockRead,
    TransactionDemand,
)
from app.modules.sales.models import Transaction, TransactionLine

router = APIRouter(prefix="/api/items", tags=["inventory"])
availability_router = APIRouter(prefix="/api/inventory", tags=["inventory"])

MAX_ITEMS_ASKED = 100

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


@availability_router.get("/availability", response_model=list[ItemAvailability])
def read_availability(
    item_id: list[uuid.UUID] = Query(default_factory=list, max_length=MAX_ITEMS_ASKED),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[ItemAvailability]:
    """On hand and available for the asked items that track stock in this organization (others are left out)."""
    ids = service.tracked_ids(db, ctx.organization_id, item_id)
    figures = service.available(db, ctx.organization_id, ids)
    return [ItemAvailability(item_id=i, on_hand=figures[i][0], available=figures[i][1]) for i in sorted(ids)]


@availability_router.get("/transactions/{transaction_id}", response_model=list[TransactionDemand])
def read_transaction_demand(
    transaction_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)
) -> list[TransactionDemand]:
    """Per stock-tracking item on the transaction: requested (all its lines), on hand, available and the shortage."""
    get_scoped_or_404(db, ctx, Transaction, transaction_id)
    requested = dict(
        db.execute(
            select(TransactionLine.item_id, func.sum(TransactionLine.quantity))
            .where(
                TransactionLine.organization_id == ctx.organization_id,
                TransactionLine.transaction_id == transaction_id,
                TransactionLine.item_id.is_not(None),
            )
            .group_by(TransactionLine.item_id)
        ).all()
    )
    ids = service.tracked_ids(db, ctx.organization_id, requested)
    figures = service.available(db, ctx.organization_id, ids)
    return [
        TransactionDemand(
            item_id=i,
            requested=requested[i],
            on_hand=figures[i][0],
            available=figures[i][1],
            shortage=max(Decimal(0), requested[i] - figures[i][1]),
        )
        for i in sorted(ids)
    ]


@availability_router.get("/transactions/{transaction_id}/fulfillment", response_model=list[LineFulfillmentRead])
def read_transaction_fulfillment(
    transaction_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)
) -> list[LineFulfillmentRead]:
    """Per stock-tracking line of a completed transaction: delivered at completion, backordered, fulfilled since."""
    get_scoped_or_404(db, ctx, Transaction, transaction_id)
    rows = list(
        db.scalars(
            select(LineFulfillment).where(
                LineFulfillment.organization_id == ctx.organization_id,
                LineFulfillment.transaction_id == transaction_id,
                LineFulfillment.cancelled_at.is_(None),
            )
        )
    )
    stock = service.on_hand(db, ctx.organization_id, [row.item_id for row in rows])
    return [
        LineFulfillmentRead(
            transaction_line_id=row.transaction_line_id,
            item_id=row.item_id,
            ordered=row.ordered,
            delivered=row.delivered,
            backordered=row.backordered,
            fulfilled_later=row.fulfilled_later,
            remaining=row.backordered - row.fulfilled_later,
            state=service.fulfillment_state(row, stock[row.item_id]),
        )
        for row in rows
    ]


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
