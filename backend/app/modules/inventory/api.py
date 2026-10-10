import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.authz import record_writer
from app.core.db import get_db
from app.core.query import contains_pattern
from app.core.tenant import TenantContext, get_tenant_context
from app.core.tenant_scope import get_scoped, get_scoped_or_404, reference_error
from app.models import Customer, Item, ItemType, Supplier, User
from app.modules.inventory import service
from app.modules.inventory.models import IncomingStock, LineFulfillment, MovementReason, StockMovement
from app.modules.inventory.schemas import (
    AllocationConfirm,
    AllocationProposal,
    BackorderRead,
    IncomingCreate,
    IncomingRead,
    InventorySummary,
    ItemAvailability,
    StockItemRead,
    LineFulfillmentRead,
    ProposedAllocation,
    Receipt,
    StockAdjustment,
    StockMovementRead,
    StockRead,
    TransactionDemand,
)
from app.modules.sales.models import Transaction, TransactionLine
from app.schemas.supplier import SupplierRef

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


STOCK_ITEMS_SHOWN = 500


@availability_router.get("/items", response_model=list[StockItemRead])
def list_stock_items(
    q: str | None = Query(default=None, max_length=255, description="Name or article number contains"),
    state: Literal["out_of_stock", "low_stock", "backordered", "incoming", "in_stock"] | None = None,
    include_inactive: bool = False,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[StockItemRead]:
    """Every product that tracks stock, by name, with on hand, committed, available, incoming and its states (any
    member). `state` keeps those in that state ("in_stock": none of the problem states)."""
    query = select(Item).where(
        Item.organization_id == ctx.organization_id, Item.type == ItemType.PRODUCT, Item.track_stock.is_(True)
    )
    if not include_inactive:
        query = query.where(Item.active.is_(True))
    if q:
        pattern = contains_pattern(q)
        query = query.where(or_(Item.name.ilike(pattern, escape="\\"), Item.sku.ilike(pattern, escape="\\")))
    items = list(db.scalars(query.order_by(Item.name, Item.id).limit(STOCK_ITEMS_SHOWN)))
    ids = [item.id for item in items]
    figures = service.available(db, ctx.organization_id, ids)
    promised = service.committed(db, ctx.organization_id, ids)
    held = service.allocated(db, ctx.organization_id, ids)
    free = service.free(db, ctx.organization_id, ids)
    coming = service.incoming(db, ctx.organization_id, ids)
    rows = []
    for item in items:
        states = service.stock_states(figures[item.id][0], item.low_stock_threshold, promised[item.id], coming[item.id])
        if state == "in_stock" and any(s in states for s in ("out_of_stock", "low_stock")):
            continue
        if state not in (None, "in_stock") and state not in states:
            continue
        rows.append(
            StockItemRead(
                item_id=item.id,
                name=item.name,
                sku=item.sku,
                unit=item.unit,
                active=item.active,
                on_hand=figures[item.id][0],
                allocated=held[item.id],
                committed=promised[item.id],
                available=free[item.id],
                incoming=coming[item.id],
                low_stock_threshold=item.low_stock_threshold,
                states=states,
            )
        )
    return rows


@availability_router.get("/availability", response_model=list[ItemAvailability])
def read_availability(
    item_id: list[uuid.UUID] = Query(default_factory=list, max_length=MAX_ITEMS_ASKED),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[ItemAvailability]:
    """On hand and available for the asked items that track stock in this organization (others are left out)."""
    ids = service.tracked_ids(db, ctx.organization_id, item_id)
    figures = service.available(db, ctx.organization_id, ids)
    promised = service.committed(db, ctx.organization_id, ids)
    held = service.allocated(db, ctx.organization_id, ids)
    free = service.free(db, ctx.organization_id, ids)
    coming = service.incoming(db, ctx.organization_id, ids)
    thresholds = dict(db.execute(select(Item.id, Item.low_stock_threshold).where(Item.organization_id == ctx.organization_id, Item.id.in_(ids))).all())
    return [
        ItemAvailability(
            item_id=i,
            on_hand=figures[i][0],
            allocated=held[i],
            committed=promised[i],
            available=free[i],
            incoming=coming[i],
            low_stock_threshold=thresholds.get(i),
            states=service.stock_states(figures[i][0], thresholds.get(i), promised[i], coming[i]),
        )
        for i in sorted(ids)
    ]


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
    others = service.allocated(db, ctx.organization_id, ids, except_transaction=transaction_id)
    free = service.free(db, ctx.organization_id, ids, except_transaction=transaction_id)
    coming = service.incoming(db, ctx.organization_id, ids)
    return [
        TransactionDemand(
            item_id=i,
            requested=requested[i],
            on_hand=figures[i][0],
            allocated=others[i],
            available=free[i],
            incoming=coming[i],
            shortage=max(Decimal(0), requested[i] - free[i]),
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


@availability_router.get("/summary", response_model=InventorySummary)
def inventory_summary(ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)) -> InventorySummary:
    """How many active stock-tracking products are out of stock or low, what waits and what is coming (any member)."""
    rows = db.execute(
        select(Item.id, Item.low_stock_threshold).where(
            Item.organization_id == ctx.organization_id, Item.active.is_(True), Item.track_stock.is_(True), Item.type == "product"
        )
    ).all()
    ids = [item_id for item_id, _ in rows]
    stock = service.on_hand(db, ctx.organization_id, ids)
    states = [service.stock_states(stock[item_id], threshold, Decimal(0), Decimal(0)) for item_id, threshold in rows]
    open_backorders = (
        LineFulfillment.organization_id == ctx.organization_id,
        LineFulfillment.cancelled_at.is_(None),
        LineFulfillment.fulfilled_later < LineFulfillment.backordered,
    )
    return InventorySummary(
        tracked_items=len(rows),
        out_of_stock=sum(1 for each in states if "out_of_stock" in each),
        low_stock=sum(1 for each in states if "low_stock" in each),
        open_backorders=db.scalar(select(func.count()).select_from(LineFulfillment).where(*open_backorders)),
        backordered_items=db.scalar(select(func.count(func.distinct(LineFulfillment.item_id))).where(*open_backorders)),
        incoming_deliveries=db.scalar(
            select(func.count())
            .select_from(IncomingStock)
            .where(IncomingStock.organization_id == ctx.organization_id, IncomingStock.cancelled_at.is_(None), IncomingStock.received < IncomingStock.quantity)
        ),
    )


# --- the backorder backlog and allocation ------------------------------------------------------------------------


@availability_router.get("/backorders", response_model=list[BackorderRead])
def list_backorders(
    item_id: uuid.UUID | None = None,
    include_closed: bool = False,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[BackorderRead]:
    """Backordered sales, oldest first (any member). By default only those still waiting for something."""
    query = (
        select(LineFulfillment, Transaction.transaction_date, Customer.name, Item.name, Item.unit)
        .join(Transaction, (Transaction.organization_id == LineFulfillment.organization_id) & (Transaction.id == LineFulfillment.transaction_id))
        .outerjoin(Customer, (Customer.organization_id == Transaction.organization_id) & (Customer.id == Transaction.billing_customer_id))
        .join(Item, (Item.organization_id == LineFulfillment.organization_id) & (Item.id == LineFulfillment.item_id))
        .where(LineFulfillment.organization_id == ctx.organization_id, LineFulfillment.backordered > 0)
    )
    if item_id is not None:
        query = query.where(LineFulfillment.item_id == item_id)
    if not include_closed:
        query = query.where(LineFulfillment.cancelled_at.is_(None), LineFulfillment.fulfilled_later < LineFulfillment.backordered)
    rows = db.execute(query.order_by(LineFulfillment.created_at, LineFulfillment.id).limit(MOVEMENTS_SHOWN)).all()
    stock = service.on_hand(db, ctx.organization_id, [row[0].item_id for row in rows])
    return [
        BackorderRead(
            fulfillment_id=row.id,
            transaction_id=row.transaction_id,
            transaction_line_id=row.transaction_line_id,
            transaction_date=transaction_date,
            customer_name=customer_name,
            item_id=row.item_id,
            item_name=item_name,
            item_unit=item_unit,
            backordered=row.backordered,
            fulfilled_later=row.fulfilled_later,
            remaining=row.backordered - row.fulfilled_later if row.cancelled_at is None else Decimal(0),
            state=service.fulfillment_state(row, stock[row.item_id]),
            created_at=row.created_at,
        )
        for row, transaction_date, customer_name, item_name, item_unit in rows
    ]


@availability_router.get("/items/{item_id}/allocation", response_model=AllocationProposal)
def propose_allocation(item_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)) -> AllocationProposal:
    """The oldest-first proposal for sharing the item's stock among its open backorders. Changes nothing."""
    get_scoped_or_404(db, ctx, Item, item_id)
    stock, proposal = service.propose_allocation(db, ctx.organization_id, item_id)
    return AllocationProposal(
        item_id=item_id,
        on_hand=stock,
        proposals=[
            ProposedAllocation(fulfillment_id=row.id, transaction_id=row.transaction_id, remaining=row.backordered - row.fulfilled_later, proposed=share)
            for row, share in proposal
        ],
    )


@availability_router.post("/items/{item_id}/allocation", response_model=list[BackorderRead])
def confirm_allocation(
    item_id: uuid.UUID, payload: AllocationConfirm, ctx: TenantContext = Depends(record_writer), db: Session = Depends(get_db)
) -> list[BackorderRead]:
    """A person confirms who gets what: each quantity is delivered to that backorder now (who and when are recorded)."""
    item = get_scoped_or_404(db, ctx, Item, item_id, for_update=True)
    service.ensure_tracked(item)
    service.allocate(db, ctx, item, [(allocation.fulfillment_id, allocation.quantity) for allocation in payload.allocations])
    db.commit()
    return list_backorders(item_id=item_id, include_closed=True, ctx=ctx, db=db)


# --- incoming stock and goods receipt -------------------------------------------------------------------------------


@availability_router.get("/incoming", response_model=list[IncomingRead])
def list_incoming(
    item_id: uuid.UUID | None = None,
    supplier_id: uuid.UUID | None = None,
    open_only: bool = True,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[IncomingRead]:
    """Incoming deliveries, the earliest expected first (any member). `open_only`: still expected, not cancelled."""
    query = (
        select(IncomingStock, Item.name, Item.unit, User.name, Supplier)
        .join(Item, (Item.organization_id == IncomingStock.organization_id) & (Item.id == IncomingStock.item_id))
        .outerjoin(User, User.id == IncomingStock.created_by)
        .outerjoin(Supplier, (Supplier.organization_id == IncomingStock.organization_id) & (Supplier.id == IncomingStock.supplier_id))
        .where(IncomingStock.organization_id == ctx.organization_id)
    )
    if item_id is not None:
        query = query.where(IncomingStock.item_id == item_id)
    if supplier_id is not None:
        query = query.where(IncomingStock.supplier_id == supplier_id)
    if open_only:
        query = query.where(IncomingStock.cancelled_at.is_(None), IncomingStock.received < IncomingStock.quantity)
    query = query.order_by(IncomingStock.expected_on.asc().nulls_last(), IncomingStock.created_at).limit(MOVEMENTS_SHOWN)
    return [_incoming_read(row, name, unit, author, supplier) for row, name, unit, author, supplier in db.execute(query)]


@availability_router.post("/incoming", response_model=IncomingRead, status_code=status.HTTP_201_CREATED)
def create_incoming(payload: IncomingCreate, ctx: TenantContext = Depends(record_writer), db: Session = Depends(get_db)) -> IncomingRead:
    """Record stock on its way (ordered from a supplier). It is not on hand until a person receives it."""
    item = get_scoped(db, ctx, Item, payload.item_id)
    if item is None:
        reference_error("item_id", "Item not found", "reference.not_found")
    service.ensure_tracked(item)
    supplier = None
    if payload.supplier_id is not None:
        # The supplier must be this organization's and active; another tenant's id reads as "not found".
        supplier = get_scoped(db, ctx, Supplier, payload.supplier_id)
        if supplier is None:
            reference_error("supplier_id", "Supplier not found", "reference.not_found")
        if not supplier.active:
            reference_error("supplier_id", "This supplier is inactive; activate it or choose another", "reference.inactive")
    row = IncomingStock(organization_id=ctx.organization_id, created_by=ctx.user.id, **payload.model_dump())
    db.add(row)
    db.commit()
    return _incoming_read(row, item.name, item.unit, ctx.user.name, supplier)


@availability_router.post("/incoming/{incoming_id}/receive", response_model=IncomingRead)
def receive_incoming(
    incoming_id: uuid.UUID, payload: Receipt, ctx: TenantContext = Depends(record_writer), db: Session = Depends(get_db)
) -> IncomingRead:
    """A person confirms that goods arrived: they become on hand through a receipt movement (all or part)."""
    row = get_scoped_or_404(db, ctx, IncomingStock, incoming_id, for_update=True)
    if row.cancelled_at is not None or row.received >= row.quantity:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This delivery is no longer expected")
    remaining = row.quantity - row.received
    quantity = payload.quantity if payload.quantity is not None else remaining
    if quantity > remaining:
        reference_error("quantity", f"Only {remaining.normalize():f} are still expected on this delivery", "incoming.too_much")
    item = service.lock_items(db, ctx, [row.item_id])[row.item_id]
    service.record_movement(db, ctx, item, quantity, MovementReason.RECEIPT, note=payload.note, incoming_stock_id=row.id)
    row.received = row.received + quantity
    db.commit()
    return _incoming_read(row, item.name, item.unit, _author(db, row.created_by), _supplier(db, row))


@availability_router.post("/incoming/{incoming_id}/cancel", response_model=IncomingRead)
def cancel_incoming(incoming_id: uuid.UUID, ctx: TenantContext = Depends(record_writer), db: Session = Depends(get_db)) -> IncomingRead:
    """What is still expected will not come. Units already received stay on hand."""
    row = get_scoped_or_404(db, ctx, IncomingStock, incoming_id, for_update=True)
    if row.cancelled_at is not None or row.received >= row.quantity:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This delivery is no longer expected")
    row.cancelled_at = clock.utcnow()
    row.cancelled_by = ctx.user.id
    db.commit()
    item = db.get(Item, row.item_id)
    return _incoming_read(row, item.name, item.unit, _author(db, row.created_by), _supplier(db, row))


def _author(db: Session, user_id: uuid.UUID | None) -> str | None:
    return db.scalar(select(User.name).where(User.id == user_id)) if user_id else None


def _supplier(db: Session, row: IncomingStock) -> Supplier | None:
    if row.supplier_id is None:
        return None
    return db.scalar(select(Supplier).where(Supplier.organization_id == row.organization_id, Supplier.id == row.supplier_id))


def _incoming_read(row: IncomingStock, item_name: str, item_unit: str, author: str | None, supplier: Supplier | None = None) -> IncomingRead:
    if row.cancelled_at is not None:
        state = "cancelled"
    elif row.received >= row.quantity:
        state = "received"
    else:
        state = "partially_received" if row.received > 0 else "expected"
    return IncomingRead(
        id=row.id,
        item_id=row.item_id,
        item_name=item_name,
        item_unit=item_unit,
        quantity=row.quantity,
        received=row.received,
        remaining=row.quantity - row.received if row.cancelled_at is None else Decimal(0),
        expected_on=row.expected_on,
        supplier=SupplierRef.model_validate(supplier) if supplier is not None else None,
        reference=row.reference,
        state=state,
        created_at=row.created_at,
        created_by_name=author,
        cancelled_at=row.cancelled_at,
    )


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
