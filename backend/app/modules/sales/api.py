import uuid
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import and_, func, select, update
from sqlalchemy.orm import Session

from app.api.deps import Pagination, pagination
from app.core import audit, clock, discounts, subjects
from app.core.authz import record_writer, roles_required
from app.core.currency import default_currency_for_new_record
from app.core.db import get_db
from app.core.lifecycle import (
    CANCEL as EVENT_CANCEL,
    COMPLETE as EVENT_COMPLETE,
    REOPEN as EVENT_REOPEN,
    ensure_valid,
    run_effects,
)
from app.core.org_time import MONTH_PATTERN, as_instant, month_range, organization_today, year_range
from app.core.query import commit_and_refresh
from app.core.tenant import TenantContext, get_tenant_context
from app.core.tenant_scope import (
    create_scoped,
    get_scoped_or_404,
    reference_error,
    resolve_reference,
    scoped_select,
)
from app.models import Customer, Item, Organization, OrganizationUser, Role, User
from app.modules.sales.models import Transaction, TransactionLine, TransactionStatus
from app.modules.sales.pricing import (
    AmountTooLarge,
    LineAmounts,
    Totals,
    calculate_line,
    calculate_totals,
    discounted_unit_price,
)
from app.modules.sales.schemas import (
    SalesSummary,
    ServiceRecord,
    AssignCurrency,
    AssignCurrencyResult,
    CurrencyStatus,
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
from app.schemas.money import CountAndAmounts, CurrencyAmount

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
        currency=tx.currency,
        line_count=len(lines),
        version=tx.version,
        header_version=tx.header_version,
        totals=_totals_read(calculate_totals(lines)),
        created_at=tx.created_at,
        updated_at=tx.updated_at,
        created_by=tx.created_by,
        updated_by=tx.updated_by,
    )


def _read_one(db: Session, ctx: TenantContext, transaction_id: uuid.UUID) -> TransactionRead:
    row = db.execute(_header_rows(ctx).where(Transaction.id == transaction_id)).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    tx, customer = row
    lines = _lines_by_transaction(db, ctx, [tx.id])[tx.id]
    return TransactionRead(
        **_summary_fields(tx, customer, lines),
        lines=_line_reads(db, ctx, lines),
    )


def _price_before_line_discount(line: TransactionLine) -> Decimal:
    """The unit price the line's own discount applies to: what a person edits next to the discount."""
    if line.line_discount_percent is None:
        return line.unit_price_ex_vat
    if line.priced_by_hand:
        return line.list_unit_price
    return discounted_unit_price(line.list_unit_price, line.catalog_discount_percent, line.customer_discount_percent)


def _line_reads(db: Session, ctx: TenantContext, lines: list[TransactionLine]) -> list[LineRead]:
    """Lines as read, with the live label of each service's subject and the name of who performed it."""
    pairs = {(line.subject_type, line.subject_id) for line in lines if line.subject_type is not None}
    labels = subjects.subject_labels(db, ctx.organization_id, pairs)
    performers = {line.performed_by for line in lines if line.performed_by is not None}
    names = dict(db.execute(select(User.id, User.name).where(User.id.in_(performers))).all()) if performers else {}
    return [
        LineRead.model_validate(line).model_copy(
            update={
                "subject_label": labels.get((line.subject_type, line.subject_id)) if line.subject_type else None,
                "performed_by_name": names.get(line.performed_by),
                "price_before_line_discount": _price_before_line_discount(line),
            }
        )
        for line in lines
    ]


def _service_values(
    db: Session, ctx: TenantContext, path: tuple[str | int, ...], *, performed_at, performed_by_user_id, subject: tuple[str, uuid.UUID] | None
) -> dict:
    """Validate the service details that are being set (each one only when given) and return them as columns."""
    values: dict = {}
    if subject is not None:
        subjects.resolve_subject(db, ctx, subject[0], subject[1], field="subject_id")
        values.update(subject_type=subject[0], subject_id=subject[1])
    if performed_by_user_id is not None:
        member = db.scalar(
            select(OrganizationUser.id).where(OrganizationUser.organization_id == ctx.organization_id, OrganizationUser.user_id == performed_by_user_id)
        )
        if member is None:
            reference_error((*path, "performed_by_user_id"), "Choose a member of this organization", "reference.not_found")
        values["performed_by"] = performed_by_user_id
    if performed_at is not None:
        values["performed_at"] = as_instant(db, ctx.organization_id, performed_at)
    return values


def _require_service_item(item: Item, path: tuple[str | int, ...]) -> None:
    if item.type != "service":
        reference_error((*path, "item_id"), "Choose a service from the catalog", "service.not_a_service")


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
        detail=f"A {tx.status} order cannot be {what}{hint}",
    )


def _changes(record, values: dict) -> bool:
    """Would applying `values` change anything? A no-op write must not move a version."""
    return any(getattr(record, field) != value for field, value in values.items())


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


def _catalog_price(
    db: Session, ctx: TenantContext, item: Item, customer_id: uuid.UUID, on_date: date, line_percent: Decimal | None = None
) -> dict:
    """The price of a catalog line: the item's price, then the temporary catalog discount active on the sale's date,
    then the billing customer's permanent discount, then the line's own discount (each layer rounded before the next).
    All of them are stored."""
    catalog = discounts.catalog_percent(db, ctx.organization_id, item.id, on_date)
    customer = discounts.customer_percent(db, ctx.organization_id, customer_id)
    return dict(
        list_unit_price=item.price_ex_vat,
        catalog_discount_percent=catalog,
        customer_discount_percent=customer,
        line_discount_percent=line_percent,
        priced_by_hand=False,
        unit_price_ex_vat=discounted_unit_price(item.price_ex_vat, catalog, customer, line_percent),
    )


def _hand_price(price: Decimal | None, line_percent: Decimal | None) -> dict:
    """A price typed by a person: no catalog or customer layer. With a line discount the typed price is the list price
    it applies to (so the step stays visible); without one there are no layers at all."""
    if line_percent is None or price is None:
        return dict(
            list_unit_price=None, catalog_discount_percent=None, customer_discount_percent=None, line_discount_percent=None,
            priced_by_hand=True, unit_price_ex_vat=price,
        )
    return dict(
        list_unit_price=price, catalog_discount_percent=None, customer_discount_percent=None, line_discount_percent=line_percent,
        priced_by_hand=True, unit_price_ex_vat=discounted_unit_price(price, None, None, line_percent),
    )


# What a catalog (or service) line takes from its item and a person cannot type: the same rule when adding a line as
# when changing it. The line's price is changed through its discount.
CATALOG_VALUES = ("description", "unit", "unit_price_ex_vat", "vat_rate")


def _refuse_catalog_values(values: dict | set, path: tuple[str | int, ...] = ()) -> None:
    for field in CATALOG_VALUES:
        if field in values:
            reference_error(
                (*path, field),
                "A catalog line takes its description, unit, price and VAT from the item; change the discount instead",
                "line.catalog_value",
            )


def _new_line_values(
    db: Session, ctx: TenantContext, line: LineCreate, path: tuple[str | int, ...] = (), *, customer_id: uuid.UUID, on_date: date
) -> dict:
    """Snapshot values for a new line: request values win, the Item fills the gaps. A catalog line whose price was not
    typed is priced with the discount layers; a typed price (or an ad-hoc line) carries no discounts."""
    item = None
    if line.item_id is not None:
        _refuse_catalog_values({field for field in CATALOG_VALUES if getattr(line, field) is not None}, path)
        item = resolve_reference(db, ctx, Item, line.item_id, (*path, "item_id"))

    def pick(requested, from_item):
        return requested if requested is not None else from_item

    description = pick(line.description, item.name if item else None)
    unit = pick(line.unit, item.unit if item else None)
    if item is not None and line.unit_price_ex_vat is None:
        pricing_values = _catalog_price(db, ctx, item, customer_id, on_date, line.line_discount_percent)
    else:
        pricing_values = _hand_price(line.unit_price_ex_vat, line.line_discount_percent)
    vat_rate = pick(line.vat_rate, item.vat_rate if item else None)
    amounts = _calculate(line.quantity, pricing_values["unit_price_ex_vat"], vat_rate, path)
    service: dict = {"kind": "standard"}
    if line.kind == "service":
        _require_service_item(item, path)
        service = {
            "kind": "service",
            "performed_at": clock.utcnow(),  # now, unless the request says when
            **_service_values(
                db, ctx, path, performed_at=line.performed_at, performed_by_user_id=line.performed_by_user_id,
                subject=(line.subject_type, line.subject_id),
            ),
        }
    return dict(
        **service,
        notes=line.notes,
        item_id=line.item_id,
        description=description,
        unit=unit,
        quantity=line.quantity,
        vat_rate=vat_rate,
        net_amount=amounts.net,
        vat_amount=amounts.vat,
        gross_amount=amounts.gross,
        **pricing_values,
    )


# --- transactions ---------------------------------------------------------------------------


@router.post("", response_model=TransactionRead, status_code=status.HTTP_201_CREATED)
def create_transaction(
    payload: TransactionCreate,
    ctx: TenantContext = Depends(record_writer),
    db: Session = Depends(get_db),
) -> TransactionRead:
    resolve_reference(
        db, ctx, Customer, payload.billing_customer_id, "billing_customer_id", label="Customer"
    )
    transaction_date = payload.transaction_date or organization_today(db, ctx.organization_id)
    # Validate every line before writing anything.
    line_values = [
        _new_line_values(db, ctx, line, ("lines", index), customer_id=payload.billing_customer_id, on_date=transaction_date)
        for index, line in enumerate(payload.lines)
    ]
    # Snapshot the organization's currency now; the row stays share-locked until the commit, so a
    # concurrent currency change either sees this transaction (and refuses) or happens first.
    currency = default_currency_for_new_record(db, ctx.organization_id)
    tx = create_scoped(
        db,
        ctx,
        Transaction,
        billing_customer_id=payload.billing_customer_id,
        currency=currency,
        transaction_date=transaction_date,
    )
    audit.created(db, ctx, tx, "transaction")
    for position, values in enumerate(line_values, start=1):
        line = create_scoped(db, ctx, TransactionLine, transaction_id=tx.id, position=position, **values)
        audit.created(db, ctx, line, "transaction_line", context=("transaction", tx.id))
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


# --- currency of transactions that predate currencies -----------------------------------------
# Declared before the "/{transaction_id}" routes so these literal paths win.


@router.get("/currency-status", response_model=CurrencyStatus)
def currency_status(
    ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)
) -> CurrencyStatus:
    return CurrencyStatus(
        default_currency=db.scalar(
            select(Organization.default_currency).where(Organization.id == ctx.organization_id)
        ),
        transactions_without_currency=db.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.organization_id == ctx.organization_id, Transaction.currency.is_(None))
        ),
    )


@router.post("/assign-currency", response_model=AssignCurrencyResult)
def assign_currency(
    payload: AssignCurrency,
    ctx: TenantContext = Depends(roles_required(Role.OWNER, Role.ADMIN)),
    db: Session = Depends(get_db),
) -> AssignCurrencyResult:
    """The one way a transaction that predates currencies gets a currency: an owner or admin
    states that those transactions were priced in the organization's default currency.

    Only transactions WITHOUT a currency are touched, so a transaction's currency never
    changes once it has one (a database trigger enforces the same). Their `version` moves so
    an open editor is told its view is stale.
    """
    currency = default_currency_for_new_record(db, ctx.organization_id)
    if payload.currency != currency:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[
                {
                    "loc": ["body", "currency"],
                    "msg": f"Must be the organization's default currency ({currency})",
                    "type": "currency.mismatch",
                }
            ],
        )
    assigned = db.execute(
        update(Transaction)
        .where(Transaction.organization_id == ctx.organization_id, Transaction.currency.is_(None))
        .values(currency=currency, version=Transaction.version + 1)
    ).rowcount
    db.commit()
    return AssignCurrencyResult(currency=currency, assigned=assigned)


@router.get("/summary", response_model=SalesSummary)
def sales_summary(
    month: str | None = Query(default=None, pattern=MONTH_PATTERN, description="YYYY-MM; the current month when absent"),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> SalesSummary:
    """Open drafts, and what was completed (per currency) and how many services were performed in the month (any member)."""
    today = organization_today(db, ctx.organization_id)
    month_start, month_end = month_range(month, today)
    drafts = db.scalar(
        select(func.count()).select_from(Transaction).where(Transaction.organization_id == ctx.organization_id, Transaction.status == DRAFT)
    )
    def period(first: date, last: date) -> tuple[CountAndAmounts, int]:
        """Completed sales (count, gross per currency) and service lines in the period."""
        within = (
            Transaction.organization_id == ctx.organization_id,
            Transaction.transaction_date >= first,
            Transaction.transaction_date <= last,
        )
        completed = db.scalar(select(func.count()).select_from(Transaction).where(*within, Transaction.status == COMPLETED))
        amounts = db.execute(
            select(Transaction.currency, func.sum(TransactionLine.gross_amount))
            .join(TransactionLine, and_(TransactionLine.organization_id == Transaction.organization_id, TransactionLine.transaction_id == Transaction.id))
            .where(*within, Transaction.status == COMPLETED, Transaction.currency.is_not(None))
            .group_by(Transaction.currency)
            .order_by(Transaction.currency)
        ).all()
        services = db.scalar(
            select(func.count())
            .select_from(TransactionLine)
            .join(Transaction, and_(Transaction.organization_id == TransactionLine.organization_id, Transaction.id == TransactionLine.transaction_id))
            .where(*within, Transaction.status != CANCELLED, TransactionLine.kind == "service")
        )
        return CountAndAmounts(count=completed, amounts=[CurrencyAmount(currency=c, amount=a) for c, a in amounts]), services

    year_start, year_end = year_range(month_start, today)
    month_sales, month_services = period(month_start, month_end)
    year_sales, year_services = period(year_start, year_end)
    return SalesSummary(
        month_start=month_start,
        month_end=month_end,
        month=month_start.strftime("%Y-%m"),
        previous_month=(month_start - timedelta(days=1)).strftime("%Y-%m"),
        next_month=None if month_end >= today else (month_end + timedelta(days=1)).strftime("%Y-%m"),
        today=today,
        drafts=drafts,
        completed_this_month=month_sales,
        services_this_month=month_services,
        year=year_start.year,
        year_start=year_start,
        year_end=year_end,
        completed_this_year=year_sales,
        services_this_year=year_services,
    )


@router.get("/services", response_model=list[ServiceRecord])
def list_services(
    subject_type: str | None = Query(default=None, max_length=64),
    subject_id: uuid.UUID | None = None,
    billing_customer_id: uuid.UUID | None = None,
    page: Pagination = Depends(pagination),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[ServiceRecord]:
    """Services performed, newest first: for one subject (a horse, a person...) and/or billed to one customer.
    Records of another organization simply never match."""
    if (subject_type is None) != (subject_id is None):
        reference_error(("query", "subject_id"), "subject_type and subject_id go together", "value_error")
    query = (
        select(TransactionLine, Transaction)
        .join(Transaction, and_(Transaction.organization_id == TransactionLine.organization_id, Transaction.id == TransactionLine.transaction_id))
        .where(TransactionLine.organization_id == ctx.organization_id, TransactionLine.kind == "service", Transaction.status != CANCELLED)
    )
    if subject_type is not None:
        query = query.where(TransactionLine.subject_type == subject_type, TransactionLine.subject_id == subject_id)
    if billing_customer_id is not None:
        query = query.where(Transaction.billing_customer_id == billing_customer_id)
    rows = db.execute(query.order_by(TransactionLine.performed_at.desc(), TransactionLine.id).limit(page.limit).offset(page.offset)).all()
    reads = {read.id: read for read in _line_reads(db, ctx, [line for line, _ in rows])}
    return [
        ServiceRecord(
            transaction_id=tx.id, transaction_date=tx.transaction_date, status=tx.status, currency=tx.currency, line_id=line.id,
            description=line.description, quantity=line.quantity, gross_amount=line.gross_amount, performed_at=line.performed_at,
            performed_by_name=reads[line.id].performed_by_name, subject_type=line.subject_type, subject_id=line.subject_id,
            subject_label=reads[line.id].subject_label, notes=line.notes,
        )
        for line, tx in rows
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
    ctx: TenantContext = Depends(record_writer),
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
    reprice = any(field in values and values[field] != getattr(tx, field) for field in ("billing_customer_id", "transaction_date"))
    audit.set_audited(db, ctx, tx, "transaction", values)
    if reprice:
        _reprice_catalog_lines(db, ctx, tx)  # in the same database transaction as the header change
    commit_and_refresh(db, tx)
    return _read_one(db, ctx, transaction_id)


def _reprice_catalog_lines(db: Session, ctx: TenantContext, tx: Transaction) -> None:
    """A new billing customer or date changes which discounts apply: lines priced from the catalog (and not by hand)
    get the layers again. Each changed line moves its version and is recorded in the history."""
    lines = db.scalars(
        select(TransactionLine).where(
            TransactionLine.organization_id == ctx.organization_id,
            TransactionLine.transaction_id == tx.id,
            TransactionLine.item_id.is_not(None),
            TransactionLine.list_unit_price.is_not(None),
            TransactionLine.priced_by_hand.is_(False),
        )
    ).all()
    for line in lines:
        catalog = discounts.catalog_percent(db, ctx.organization_id, line.item_id, tx.transaction_date)
        customer = discounts.customer_percent(db, ctx.organization_id, tx.billing_customer_id)
        price = discounted_unit_price(line.list_unit_price, catalog, customer, line.line_discount_percent)
        if (catalog, customer, price) == (line.catalog_discount_percent, line.customer_discount_percent, line.unit_price_ex_vat):
            continue
        amounts = _calculate(line.quantity, price, line.vat_rate)
        audit.set_audited(
            db, ctx, line, "transaction_line",
            dict(
                catalog_discount_percent=catalog, customer_discount_percent=customer, unit_price_ex_vat=price,
                net_amount=amounts.net, vat_amount=amounts.vat, gross_amount=amounts.gross, version=line.version + 1,
            ),
            context=("transaction", tx.id),
        )


@router.delete("/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_transaction(
    transaction_id: uuid.UUID,
    if_match: str | None = Header(default=None),
    ctx: TenantContext = Depends(record_writer),
    db: Session = Depends(get_db),
) -> Response:
    tx = _lock(db, ctx, transaction_id)
    _require_draft(tx, "deleted; cancel it instead" if tx.status == COMPLETED else "deleted")
    ensure_current(if_match, tx.version, "transaction", tx.id)
    audit.deleted(db, ctx, tx, "transaction")
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
    event: str,
    allowed_from: tuple[TransactionStatus, ...],
    to: TransactionStatus,
    if_match: str | None,
) -> TransactionRead:
    tx = _lock(db, ctx, transaction_id)
    if tx.status not in allowed_from:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"A {tx.status} order cannot be {verb}"
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
                detail="An order needs at least one line to be completed",
            )
    # Anything registered on the core lifecycle seam may object: required custom fields to a
    # completion, and (later) whatever holds a claim on a completed transaction to a reopen or a
    # cancel. The row is locked, so what the validators see cannot change underneath us before
    # the status is written.
    ensure_valid(db, ctx, event, "transaction", tx.id)
    before = audit.snapshot(tx)
    tx.status = to
    tx.version += 1
    audit.updated(db, ctx, tx, "transaction", before, action=verb)
    db.flush()
    # Whatever reacts to the step (Inventory delivers or returns stock) does so now: in this database
    # transaction, under this row lock. If an effect refuses, nothing of the step is committed.
    run_effects(db, ctx, event, "transaction", tx.id)
    commit_and_refresh(db, tx)
    return _read_one(db, ctx, transaction_id)


@router.post("/{transaction_id}/complete", response_model=TransactionRead)
def complete_transaction(
    transaction_id: uuid.UUID,
    if_match: str | None = Header(default=None),
    ctx: TenantContext = Depends(record_writer),
    db: Session = Depends(get_db),
) -> TransactionRead:
    return _transition(
        db, ctx, transaction_id, verb="completed", event=EVENT_COMPLETE, allowed_from=(DRAFT,), to=COMPLETED, if_match=if_match
    )


@router.post("/{transaction_id}/reopen", response_model=TransactionRead)
def reopen_transaction(
    transaction_id: uuid.UUID,
    if_match: str | None = Header(default=None),
    ctx: TenantContext = Depends(record_writer),
    db: Session = Depends(get_db),
) -> TransactionRead:
    return _transition(
        db, ctx, transaction_id, verb="reopened", event=EVENT_REOPEN, allowed_from=(COMPLETED,), to=DRAFT, if_match=if_match
    )


@router.post("/{transaction_id}/cancel", response_model=TransactionRead)
def cancel_transaction(
    transaction_id: uuid.UUID,
    if_match: str | None = Header(default=None),
    ctx: TenantContext = Depends(record_writer),
    db: Session = Depends(get_db),
) -> TransactionRead:
    return _transition(
        db, ctx, transaction_id, verb="cancelled", event=EVENT_CANCEL, allowed_from=(DRAFT, COMPLETED), to=CANCELLED, if_match=if_match
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
    ctx: TenantContext = Depends(record_writer),
    db: Session = Depends(get_db),
) -> LineRead:
    tx = _lock(db, ctx, transaction_id)
    _require_draft(tx)
    values = _new_line_values(db, ctx, payload, customer_id=tx.billing_customer_id, on_date=tx.transaction_date)
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
    audit.created(db, ctx, line, "transaction_line", context=("transaction", tx.id))
    audit.stamp(tx, ctx)
    commit_and_refresh(db, line)
    return _line_reads(db, ctx, [line])[0]


@router.patch("/{transaction_id}/lines/{line_id}", response_model=LineRead)
def update_line(
    transaction_id: uuid.UUID,
    line_id: uuid.UUID,
    payload: LineUpdate,
    if_match: str | None = Header(default=None),
    ctx: TenantContext = Depends(record_writer),
    db: Session = Depends(get_db),
) -> LineRead:
    tx = _lock(db, ctx, transaction_id)
    _require_draft(tx)
    line = _get_line(db, ctx, tx, line_id)
    ensure_current(if_match, line.version, "transaction_line", line.id)
    values = payload.model_dump(exclude_unset=True)

    # The same rules as when the line was added: a catalog or service line keeps its catalog values (another item of
    # the same kind may be chosen), an ad-hoc line keeps having none; a line never changes kind.
    if line.item_id is not None:
        _refuse_catalog_values(values)
        if "item_id" in values and values["item_id"] is None:
            reference_error(("item_id",), "A catalog line stays a catalog line; add an ad-hoc line instead", "line.kind_change")
    elif values.get("item_id") is not None:
        reference_error(("item_id",), "An ad-hoc line cannot become a catalog line; add a catalog line instead", "line.kind_change")

    service_keys = {"performed_at", "performed_by_user_id", "subject_type", "subject_id"}
    if service_keys & values.keys():
        if line.kind != "service":
            reference_error(("performed_at",), "Only a service line has service details", "service.not_a_service_line")
        subject = (values.pop("subject_type"), values.pop("subject_id")) if "subject_type" in values else None
        if subject == (line.subject_type, line.subject_id):
            subject = None  # unchanged: an existing subject stays valid even if it was deactivated since
        performer = values.pop("performed_by_user_id", None)
        if "performed_by_user_id" in payload.model_fields_set and performer is None:
            values["performed_by"] = None  # cleared
        values.update(
            _service_values(db, ctx, (), performed_at=values.pop("performed_at", None), performed_by_user_id=performer, subject=subject)
        )
    if line.kind == "service" and "item_id" in values:
        if values["item_id"] is None:
            reference_error(("item_id",), "A service line keeps its catalog service", "service.needs_item")
    line_percent = values.pop("line_discount_percent") if "line_discount_percent" in values else line.line_discount_percent
    if "unit_price_ex_vat" in values:
        # A price typed by a person replaces the catalog's price and its layers; the line's discount applies to it.
        values.update(_hand_price(values.pop("unit_price_ex_vat"), line_percent))
    elif "line_discount_percent" in payload.model_fields_set and not (values.get("item_id") is not None and values["item_id"] != line.item_id):
        if line.priced_by_hand or line.list_unit_price is None:
            base = line.list_unit_price if line.list_unit_price is not None else line.unit_price_ex_vat
            values.update(_hand_price(base, line_percent))
        else:
            values.update(
                line_discount_percent=line_percent,
                unit_price_ex_vat=discounted_unit_price(line.list_unit_price, line.catalog_discount_percent, line.customer_discount_percent, line_percent),
            )
    if values.get("item_id") is not None and values["item_id"] != line.item_id:
        # A different item: copy its values for everything not overridden in this request.
        item = resolve_reference(db, ctx, Item, values["item_id"], "item_id")
        if line.kind == "service":
            _require_service_item(item, ())
        values.setdefault("description", item.name)
        values.setdefault("unit", item.unit)
        values.setdefault("vat_rate", item.vat_rate)
        if "priced_by_hand" not in values:  # no typed price in this request: the new item's catalog price
            values.update(_catalog_price(db, ctx, item, tx.billing_customer_id, tx.transaction_date, line_percent))

    amounts = _calculate(
        values.get("quantity", line.quantity),
        values.get("unit_price_ex_vat", line.unit_price_ex_vat),
        values.get("vat_rate", line.vat_rate),
    )
    values.update(net_amount=amounts.net, vat_amount=amounts.vat, gross_amount=amounts.gross)
    if _changes(line, values):
        values["version"] = line.version + 1
        tx.version += 1
        audit.stamp(tx, ctx)
    audit.apply_audited_update(db, ctx, line, "transaction_line", values, context=("transaction", tx.id))
    return _line_reads(db, ctx, [line])[0]


@router.delete("/{transaction_id}/lines/{line_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_line(
    transaction_id: uuid.UUID,
    line_id: uuid.UUID,
    if_match: str | None = Header(default=None),
    ctx: TenantContext = Depends(record_writer),
    db: Session = Depends(get_db),
) -> Response:
    tx = _lock(db, ctx, transaction_id)
    _require_draft(tx)
    line = _get_line(db, ctx, tx, line_id)
    ensure_current(if_match, line.version, "transaction_line", line.id)
    tx.version += 1
    audit.stamp(tx, ctx)
    audit.deleted(db, ctx, line, "transaction_line", context=("transaction", tx.id))
    db.delete(line)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
