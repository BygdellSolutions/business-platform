import uuid
from collections.abc import Sequence
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.api.deps import Pagination, pagination
from app.core.db import get_db
from app.core.lifecycle import COMPLETE as EVENT_COMPLETE, ensure_valid
from app.core.query import apply_update, commit_and_refresh
from app.core.tenant import TenantContext, get_tenant_context
from app.core.tenant_scope import (
    create_scoped,
    get_scoped_or_404,
    resolve_reference,
    scoped_select,
)
from app.models import Customer, Item
from app.modules.sales.models import Transaction, TransactionLine, TransactionStatus
from app.modules.sales.pricing import (
    AmountTooLarge,
    LineAmounts,
    Totals,
    calculate_line,
    calculate_totals,
)
from app.modules.sales.schemas import (
    LineCreate,
    LineRead,
    LineUpdate,
    TotalsRead,
    TransactionCreate,
    TransactionRead,
    TransactionSummary,
    TransactionUpdate,
    VatBreakdownRead,
)
from app.modules.sales.versioning import ensure_current
from app.schemas.customer import CustomerRef

router = APIRouter(prefix="/api/transactions", tags=["transactions"])

DRAFT = TransactionStatus.DRAFT
COMPLETED = TransactionStatus.COMPLETED
CANCELLED = TransactionStatus.CANCELLED


# --- reading ---------------------------------------------------------------------------


def _header_rows(ctx: TenantContext):
    """Transactions of the active organization with their billing customer.

    Starts from scoped_select; the join matches on (organization_id, id), the pair the
    composite foreign key guarantees, so it cannot cross tenants.
    """
    return (
        scoped_select(Transaction, ctx)
        .add_columns(Customer)
        .join(
            Customer,
            and_(
                Customer.organization_id == Transaction.organization_id,
                Customer.id == Transaction.billing_customer_id,
            ),
        )
    )


def _lines_by_transaction(
    db: Session, ctx: TenantContext, transaction_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[TransactionLine]]:
    grouped: dict[uuid.UUID, list[TransactionLine]] = {tid: [] for tid in transaction_ids}
    if transaction_ids:
        query = (
            scoped_select(TransactionLine, ctx)
            .where(TransactionLine.transaction_id.in_(transaction_ids))
            .order_by(TransactionLine.position, TransactionLine.id)
        )
        for line in db.scalars(query):
            grouped[line.transaction_id].append(line)
    return grouped


def _totals_read(totals: Totals) -> TotalsRead:
    return TotalsRead(
        net_amount=totals.net_amount,
        vat_amount=totals.vat_amount,
        gross_amount=totals.gross_amount,
        vat_breakdown=[
            VatBreakdownRead(
                vat_rate=row.vat_rate, net_amount=row.net_amount, vat_amount=row.vat_amount
            )
            for row in totals.vat_breakdown
        ],
    )


def _summary_fields(tx: Transaction, customer: Customer, lines: Sequence[TransactionLine]) -> dict:
    return dict(
        id=tx.id,
        billing_customer_id=tx.billing_customer_id,
        billing_customer=CustomerRef.model_validate(customer),
        transaction_date=tx.transaction_date,
        status=tx.status,
        line_count=len(lines),
        version=tx.version,
        header_version=tx.header_version,
        totals=_totals_read(calculate_totals(lines)),
        created_at=tx.created_at,
        updated_at=tx.updated_at,
    )


def _read_one(db: Session, ctx: TenantContext, transaction_id: uuid.UUID) -> TransactionRead:
    row = db.execute(_header_rows(ctx).where(Transaction.id == transaction_id)).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    tx, customer = row
    lines = _lines_by_transaction(db, ctx, [tx.id])[tx.id]
    return TransactionRead(
        **_summary_fields(tx, customer, lines),
        lines=[LineRead.model_validate(line) for line in lines],
    )


# --- rules shared by the mutating endpoints ------------------------------------------------


def _lock(db: Session, ctx: TenantContext, transaction_id: uuid.UUID) -> Transaction:
    """The transaction, row-locked so a status change and a line edit cannot interleave."""
    return get_scoped_or_404(db, ctx, Transaction, transaction_id, for_update=True)


def _require_draft(tx: Transaction, what: str = "changed") -> None:
    if tx.status == DRAFT:
        return
    hint = "; reopen it first" if tx.status == COMPLETED else ""
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        detail=f"A {tx.status} transaction cannot be {what}{hint}",
    )


def _changes(record, values: dict) -> bool:
    """Would applying `values` change anything? A no-op write must not move a version."""
    return any(getattr(record, field) != value for field, value in values.items())


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _calculate(
    quantity: Decimal, price: Decimal, vat_rate: Decimal, path: tuple[str | int, ...] = ()
) -> LineAmounts:
    try:
        return calculate_line(quantity, price, vat_rate)
    except AmountTooLarge:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[
                {
                    "loc": ["body", *path, "quantity"],
                    "msg": "The line amount is too large",
                    "type": "amount.too_large",
                }
            ],
        )


def _new_line_values(
    db: Session, ctx: TenantContext, line: LineCreate, path: tuple[str | int, ...] = ()
) -> dict:
    """Snapshot values for a new line: request values win, the Item fills the gaps."""
    item = None
    if line.item_id is not None:
        item = resolve_reference(db, ctx, Item, line.item_id, (*path, "item_id"))

    def pick(requested, from_item):
        return requested if requested is not None else from_item

    description = pick(line.description, item.name if item else None)
    unit = pick(line.unit, item.unit if item else None)
    price = pick(line.unit_price_ex_vat, item.price_ex_vat if item else None)
    vat_rate = pick(line.vat_rate, item.vat_rate if item else None)
    amounts = _calculate(line.quantity, price, vat_rate, path)
    return dict(
        item_id=line.item_id,
        description=description,
        unit=unit,
        quantity=line.quantity,
        unit_price_ex_vat=price,
        vat_rate=vat_rate,
        net_amount=amounts.net,
        vat_amount=amounts.vat,
        gross_amount=amounts.gross,
    )


# --- transactions ---------------------------------------------------------------------------


@router.post("", response_model=TransactionRead, status_code=status.HTTP_201_CREATED)
def create_transaction(
    payload: TransactionCreate,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> TransactionRead:
    resolve_reference(
        db, ctx, Customer, payload.billing_customer_id, "billing_customer_id", label="Customer"
    )
    # Validate every line before writing anything.
    line_values = [
        _new_line_values(db, ctx, line, ("lines", index))
        for index, line in enumerate(payload.lines)
    ]
    tx = create_scoped(
        db,
        ctx,
        Transaction,
        billing_customer_id=payload.billing_customer_id,
        transaction_date=payload.transaction_date or _today(),
    )
    for position, values in enumerate(line_values, start=1):
        create_scoped(db, ctx, TransactionLine, transaction_id=tx.id, position=position, **values)
    db.commit()
    return _read_one(db, ctx, tx.id)


@router.get("", response_model=list[TransactionSummary])
def list_transactions(
    status_filter: TransactionStatus | None = Query(default=None, alias="status"),
    billing_customer_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: Pagination = Depends(pagination),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[TransactionSummary]:
    # A filter id from another organization simply matches nothing in this one.
    query = _header_rows(ctx)
    if status_filter is not None:
        query = query.where(Transaction.status == status_filter)
    if billing_customer_id is not None:
        query = query.where(Transaction.billing_customer_id == billing_customer_id)
    if date_from is not None:
        query = query.where(Transaction.transaction_date >= date_from)
    if date_to is not None:
        query = query.where(Transaction.transaction_date <= date_to)
    query = (
        query.order_by(
            Transaction.transaction_date.desc(), Transaction.created_at.desc(), Transaction.id
        )
        .limit(page.limit)
        .offset(page.offset)
    )
    rows = db.execute(query).all()
    lines = _lines_by_transaction(db, ctx, [tx.id for tx, _ in rows])
    return [
        TransactionSummary(**_summary_fields(tx, customer, lines[tx.id])) for tx, customer in rows
    ]


@router.get("/{transaction_id}", response_model=TransactionRead)
def read_transaction(
    transaction_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> TransactionRead:
    return _read_one(db, ctx, transaction_id)


@router.patch("/{transaction_id}", response_model=TransactionRead)
def update_transaction(
    transaction_id: uuid.UUID,
    payload: TransactionUpdate,
    if_match: str | None = Header(default=None),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> TransactionRead:
    tx = _lock(db, ctx, transaction_id)
    _require_draft(tx)
    ensure_current(if_match, tx.header_version, "transaction", tx.id)
    values = payload.model_dump(exclude_unset=True)
    if "billing_customer_id" in values and values["billing_customer_id"] != tx.billing_customer_id:
        resolve_reference(
            db, ctx, Customer, values["billing_customer_id"], "billing_customer_id", label="Customer"
        )
    if _changes(tx, values):
        values.update(header_version=tx.header_version + 1, version=tx.version + 1)
    apply_update(db, tx, values)
    return _read_one(db, ctx, transaction_id)


@router.delete("/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_transaction(
    transaction_id: uuid.UUID,
    if_match: str | None = Header(default=None),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> Response:
    tx = _lock(db, ctx, transaction_id)
    _require_draft(tx, "deleted; cancel it instead" if tx.status == COMPLETED else "deleted")
    ensure_current(if_match, tx.version, "transaction", tx.id)
    db.delete(tx)  # its lines go with it (ON DELETE CASCADE)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- lifecycle actions --------------------------------------------------------------------------


def _transition(
    db: Session,
    ctx: TenantContext,
    transaction_id: uuid.UUID,
    *,
    verb: str,
    allowed_from: tuple[TransactionStatus, ...],
    to: TransactionStatus,
    if_match: str | None,
) -> TransactionRead:
    tx = _lock(db, ctx, transaction_id)
    if tx.status not in allowed_from:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"A {tx.status} transaction cannot be {verb}"
        )
    # A lifecycle step is a decision about everything the caller was looking at: the header,
    # the lines and the totals. It is refused if any of that changed since.
    ensure_current(if_match, tx.version, "transaction", tx.id)
    if to == COMPLETED:
        line_count = db.scalar(
            select(func.count())
            .select_from(TransactionLine)
            .where(
                TransactionLine.organization_id == ctx.organization_id,
                TransactionLine.transaction_id == tx.id,
            )
        )
        if line_count == 0:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="A transaction needs at least one line to be completed",
            )
        # Anything registered on the core lifecycle seam may object (for example required
        # custom fields). The row is locked, so what the validators see cannot change
        # underneath us before the status is written.
        ensure_valid(db, ctx, EVENT_COMPLETE, "transaction", tx.id)
    tx.status = to
    tx.version += 1
    commit_and_refresh(db, tx)
    return _read_one(db, ctx, transaction_id)


@router.post("/{transaction_id}/complete", response_model=TransactionRead)
def complete_transaction(
    transaction_id: uuid.UUID,
    if_match: str | None = Header(default=None),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> TransactionRead:
    return _transition(
        db, ctx, transaction_id, verb="completed", allowed_from=(DRAFT,), to=COMPLETED, if_match=if_match
    )


@router.post("/{transaction_id}/reopen", response_model=TransactionRead)
def reopen_transaction(
    transaction_id: uuid.UUID,
    if_match: str | None = Header(default=None),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> TransactionRead:
    return _transition(
        db, ctx, transaction_id, verb="reopened", allowed_from=(COMPLETED,), to=DRAFT, if_match=if_match
    )


@router.post("/{transaction_id}/cancel", response_model=TransactionRead)
def cancel_transaction(
    transaction_id: uuid.UUID,
    if_match: str | None = Header(default=None),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> TransactionRead:
    return _transition(
        db, ctx, transaction_id, verb="cancelled", allowed_from=(DRAFT, COMPLETED), to=CANCELLED, if_match=if_match
    )


# --- lines (draft transactions only) ------------------------------------------------------------


def _get_line(
    db: Session, ctx: TenantContext, tx: Transaction, line_id: uuid.UUID
) -> TransactionLine:
    # Both the organization AND the transaction in the path must match the line.
    line = db.scalar(
        scoped_select(TransactionLine, ctx).where(
            TransactionLine.id == line_id, TransactionLine.transaction_id == tx.id
        )
    )
    if line is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    return line


@router.post(
    "/{transaction_id}/lines", response_model=LineRead, status_code=status.HTTP_201_CREATED
)
def add_line(
    transaction_id: uuid.UUID,
    payload: LineCreate,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> TransactionLine:
    tx = _lock(db, ctx, transaction_id)
    _require_draft(tx)
    values = _new_line_values(db, ctx, payload)
    tx.version += 1  # no precondition (adding commutes with other edits), but the transaction changed
    last_position = db.scalar(
        select(func.coalesce(func.max(TransactionLine.position), 0)).where(
            TransactionLine.organization_id == ctx.organization_id,
            TransactionLine.transaction_id == tx.id,
        )
    )
    line = create_scoped(
        db, ctx, TransactionLine, transaction_id=tx.id, position=last_position + 1, **values
    )
    commit_and_refresh(db, line)
    return line


@router.patch("/{transaction_id}/lines/{line_id}", response_model=LineRead)
def update_line(
    transaction_id: uuid.UUID,
    line_id: uuid.UUID,
    payload: LineUpdate,
    if_match: str | None = Header(default=None),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> TransactionLine:
    tx = _lock(db, ctx, transaction_id)
    _require_draft(tx)
    line = _get_line(db, ctx, tx, line_id)
    ensure_current(if_match, line.version, "transaction_line", line.id)
    values = payload.model_dump(exclude_unset=True)

    if values.get("item_id") is not None and values["item_id"] != line.item_id:
        # A different item: copy its values for everything not overridden in this request.
        item = resolve_reference(db, ctx, Item, values["item_id"], "item_id")
        values.setdefault("description", item.name)
        values.setdefault("unit", item.unit)
        values.setdefault("unit_price_ex_vat", item.price_ex_vat)
        values.setdefault("vat_rate", item.vat_rate)

    amounts = _calculate(
        values.get("quantity", line.quantity),
        values.get("unit_price_ex_vat", line.unit_price_ex_vat),
        values.get("vat_rate", line.vat_rate),
    )
    values.update(net_amount=amounts.net, vat_amount=amounts.vat, gross_amount=amounts.gross)
    if _changes(line, values):
        values["version"] = line.version + 1
        tx.version += 1
    apply_update(db, line, values)
    return line


@router.delete("/{transaction_id}/lines/{line_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_line(
    transaction_id: uuid.UUID,
    line_id: uuid.UUID,
    if_match: str | None = Header(default=None),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> Response:
    tx = _lock(db, ctx, transaction_id)
    _require_draft(tx)
    line = _get_line(db, ctx, tx, line_id)
    ensure_current(if_match, line.version, "transaction_line", line.id)
    tx.version += 1
    db.delete(line)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
