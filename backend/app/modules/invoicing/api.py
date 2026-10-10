import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import Pagination, Sorting, pagination, sorted_by, sorting
from app.core.authz import roles_required
from app.core.db import get_db
from app.core.org_time import MONTH_PATTERN, month_range, organization_today, year_range
from app.core.query import contains_pattern
from app.core.tenant import TenantContext, get_tenant_context
from app.core.tenant_scope import scoped_select
from app.models import Customer, Role
from app.modules.invoicing import credits, payments, returns, service
from app.modules.invoicing.pdf import service as pdf_service
from app.modules.invoicing.pdf.filename import content_disposition
from app.modules.invoicing.models import OPEN_RETURN_STATES, Invoice, InvoicePayment, InvoiceReturn, InvoiceStatus, InvoiceTransaction
from app.modules.invoicing.schemas import (
    GoodsReceived,
    ReturnCreate,
    ReturnFollowUp,
    ReturnNote,
    ReturnRejection,
    ReturnStep,
    CreditNoteCreate,
    CreditNoteRead,
    MAX_TRANSACTIONS_PER_INVOICE,
    InvoiceableTotals,
    InvoiceableTransaction,
    InvoiceCreate,
    InvoiceRead,
    InvoiceStateRead,
    InvoiceSummary,
    InvoiceUpdate,
    InvoicingSummary,
    PaymentCreate,
    PaymentReversal,
    RefundCreate,
)
from app.modules.sales.models import Transaction, TransactionLine, TransactionStatus
from app.modules.sales.pricing import calculate_totals
from app.schemas.customer import CustomerRef
from app.schemas.money import CountAndAmounts, CurrencyAmount

router = APIRouter(prefix="/api/invoices", tags=["invoices"])
invoiceable_router = APIRouter(prefix="/api/invoiceable-transactions", tags=["invoices"])

# Reading is open to every member of the organization; changing is for the people who do the books.
mutators = roles_required(Role.OWNER, Role.ADMIN, Role.ACCOUNTANT)


# --- what can be invoiced ----------------------------------------------------------------------------------------


def _invoiceable_sorts() -> dict:
    """The new-invoice list's sort columns; amounts are sums of the stored line amounts, as the list shows them."""
    def line_sum(column):
        return func.coalesce(
            select(func.sum(column))
            .where(TransactionLine.organization_id == Transaction.organization_id, TransactionLine.transaction_id == Transaction.id)
            .scalar_subquery(),
            0,
        )

    return {
        "number": Transaction.number,
        "date": Transaction.transaction_date,
        "customer": func.lower(Customer.name),
        "currency": Transaction.currency,
        "lines": line_sum(1),
        "net": line_sum(TransactionLine.net_amount),
        "vat": line_sum(TransactionLine.vat_amount),
        "gross": line_sum(TransactionLine.gross_amount),
    }


@invoiceable_router.get("", response_model=list[InvoiceableTransaction])
def list_invoiceable_transactions(
    customer_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: Pagination = Depends(pagination),
    sort: Sorting = Depends(sorting),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[InvoiceableTransaction]:
    """Completed transactions with a currency that are on no invoice, draft or issued."""
    reserved = exists().where(
        InvoiceTransaction.organization_id == Transaction.organization_id,
        InvoiceTransaction.transaction_id == Transaction.id,
    )
    query = (
        scoped_select(Transaction, ctx)
        .add_columns(Customer)
        .join(
            Customer,
            and_(Customer.organization_id == Transaction.organization_id, Customer.id == Transaction.billing_customer_id),
        )
        .where(Transaction.status == TransactionStatus.COMPLETED, Transaction.currency.is_not(None), ~reserved)
    )
    if customer_id is not None:
        query = query.where(Transaction.billing_customer_id == customer_id)
    if date_from is not None:
        query = query.where(Transaction.transaction_date >= date_from)
    if date_to is not None:
        query = query.where(Transaction.transaction_date <= date_to)
    newest_first = (Transaction.transaction_date.desc(), Transaction.created_at.desc(), Transaction.id)
    rows = db.execute(
        sorted_by(query, sort, _invoiceable_sorts(), newest_first)
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    lines: dict[uuid.UUID, list[TransactionLine]] = {tx.id: [] for tx, _ in rows}
    if lines:
        for line in db.scalars(scoped_select(TransactionLine, ctx).where(TransactionLine.transaction_id.in_(list(lines)))):
            lines[line.transaction_id].append(line)
    result = []
    for tx, customer in rows:
        totals = calculate_totals(lines[tx.id])
        result.append(
            InvoiceableTransaction(
                id=tx.id,
                number=tx.number,
                transaction_date=tx.transaction_date,
                billing_customer_id=tx.billing_customer_id,
                billing_customer=CustomerRef.model_validate(customer),
                currency=tx.currency,
                line_count=len(lines[tx.id]),
                version=tx.version,
                totals=InvoiceableTotals(
                    net_amount=totals.net_amount, vat_amount=totals.vat_amount, gross_amount=totals.gross_amount
                ),
            )
        )
    return result


# --- invoices ------------------------------------------------------------------------------------------------------


@router.get("/summary", response_model=InvoicingSummary)
def invoicing_summary(
    month: str | None = Query(default=None, pattern=MONTH_PATTERN, description="YYYY-MM; the current month when absent"),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> InvoicingSummary:
    """What is to do and pending now (ready to invoice, drafts, past due, unpaid), and what was issued and paid in the
    month (any member)."""
    today = organization_today(db, ctx.organization_id)
    month_start, month_end = month_range(month, today)
    year_start, year_end = year_range(month_start, today)
    reserved = exists().where(InvoiceTransaction.organization_id == Transaction.organization_id, InvoiceTransaction.transaction_id == Transaction.id)
    ready = (Transaction.organization_id == ctx.organization_id, Transaction.status == TransactionStatus.COMPLETED, Transaction.currency.is_not(None), ~reserved)
    ready_count = db.scalar(select(func.count()).select_from(Transaction).where(*ready))
    ready_amounts = db.execute(
        select(Transaction.currency, func.sum(TransactionLine.gross_amount))
        .join(TransactionLine, and_(TransactionLine.organization_id == Transaction.organization_id, TransactionLine.transaction_id == Transaction.id))
        .where(*ready)
        .group_by(Transaction.currency)
        .order_by(Transaction.currency)
    ).all()

    def invoices(*conditions) -> CountAndAmounts:
        where = (Invoice.organization_id == ctx.organization_id, *conditions)
        count = db.scalar(select(func.count()).select_from(Invoice).where(*where))
        # After credit notes: what the invoices are worth now.
        amounts = db.execute(
            select(Invoice.currency, func.sum(payments.owed_expression())).where(*where).group_by(Invoice.currency).order_by(Invoice.currency)
        ).all()
        return CountAndAmounts(count=count, amounts=[CurrencyAmount(currency=c, amount=a) for c, a in amounts])

    return InvoicingSummary(
        ready_to_invoice=CountAndAmounts(count=ready_count, amounts=[CurrencyAmount(currency=c, amount=a) for c, a in ready_amounts]),
        draft_invoices=db.scalar(select(func.count()).select_from(Invoice).where(Invoice.organization_id == ctx.organization_id, Invoice.status == InvoiceStatus.DRAFT)),
        month_start=month_start,
        month_end=month_end,
        issued_this_month=invoices(Invoice.status == InvoiceStatus.ISSUED, Invoice.invoice_date >= month_start, Invoice.invoice_date <= month_end),
        past_due=_outstanding(db, ctx, Invoice.due_date.is_not(None), Invoice.due_date < today),
        unpaid=_outstanding(db, ctx),
        not_yet_due=_outstanding(db, ctx, or_(Invoice.due_date.is_(None), Invoice.due_date >= today)),
        partially_paid=_outstanding(db, ctx, payments.paid_sum_expression() > 0),
        refund_due=_refund_due(db, ctx),
        returns_open=db.scalar(
            select(func.count()).select_from(InvoiceReturn).where(InvoiceReturn.organization_id == ctx.organization_id, InvoiceReturn.state.in_(OPEN_RETURN_STATES))
        ),
        returns_follow_up_due=db.scalar(
            select(func.count())
            .select_from(InvoiceReturn)
            .where(InvoiceReturn.organization_id == ctx.organization_id, InvoiceReturn.state.in_(OPEN_RETURN_STATES), InvoiceReturn.follow_up_on <= today)
        ),
        paid_this_month=_paid_between(db, ctx, month_start, month_end),
        issued_this_year=invoices(Invoice.status == InvoiceStatus.ISSUED, Invoice.invoice_date >= year_start, Invoice.invoice_date <= year_end),
        paid_this_year=_paid_between(db, ctx, year_start, year_end),
    )


def _outstanding(db: Session, ctx: TenantContext, *conditions) -> CountAndAmounts:
    """Issued invoices not fully paid after credits (and matching `conditions`); the amounts are what is still outstanding."""
    paid = payments.paid_sum_expression()
    owed = payments.owed_expression()
    where = (
        Invoice.organization_id == ctx.organization_id,
        Invoice.status == InvoiceStatus.ISSUED,
        paid < owed,
        *conditions,
    )
    count = db.scalar(select(func.count()).select_from(Invoice).where(*where))
    amounts = db.execute(select(Invoice.currency, func.sum(owed - paid)).where(*where).group_by(Invoice.currency).order_by(Invoice.currency)).all()
    return CountAndAmounts(count=count, amounts=[CurrencyAmount(currency=c, amount=a) for c, a in amounts])


def _refund_due(db: Session, ctx: TenantContext) -> CountAndAmounts:
    """Issued invoices paid beyond what is owed after their credit notes; the amounts are what is to be paid back."""
    paid = payments.paid_sum_expression()
    owed = payments.owed_expression()
    where = (Invoice.organization_id == ctx.organization_id, Invoice.status == InvoiceStatus.ISSUED, paid > owed)
    count = db.scalar(select(func.count()).select_from(Invoice).where(*where))
    amounts = db.execute(select(Invoice.currency, func.sum(paid - owed)).where(*where).group_by(Invoice.currency).order_by(Invoice.currency)).all()
    return CountAndAmounts(count=count, amounts=[CurrencyAmount(currency=c, amount=a) for c, a in amounts])


def _paid_between(db: Session, ctx: TenantContext, start: date, end: date) -> CountAndAmounts:
    """Payments dated in the period, per currency (reversals and refunds subtracted); the count is of payments only."""
    where = (
        InvoicePayment.organization_id == ctx.organization_id,
        InvoicePayment.paid_on >= start,
        InvoicePayment.paid_on <= end,
    )
    joined = and_(Invoice.organization_id == InvoicePayment.organization_id, Invoice.id == InvoicePayment.invoice_id)
    count = db.scalar(select(func.count()).select_from(InvoicePayment).where(*where, InvoicePayment.reverses_payment_id.is_(None), InvoicePayment.amount > 0))
    amounts = db.execute(
        select(Invoice.currency, func.sum(InvoicePayment.amount)).join(Invoice, joined).where(*where).group_by(Invoice.currency).order_by(Invoice.currency)
    ).all()
    return CountAndAmounts(count=count, amounts=[CurrencyAmount(currency=c, amount=a) for c, a in amounts if a != 0])


@router.get("/by-transaction", response_model=list[InvoiceStateRead])
def invoice_state_of_transactions(
    ids: str = Query(description="Comma-separated transaction ids"),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[InvoiceStateRead]:
    """Whether each transaction is on an invoice, for pages that show Sales records (Sales itself
    stays unaware). An id that is unknown, or belongs to another organization, answers `none`,
    exactly like a transaction that is on no invoice."""
    try:
        wanted = list(dict.fromkeys(uuid.UUID(part) for part in ids.split(",") if part.strip()))
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[{"loc": ["query", "ids"], "msg": "must be comma-separated ids", "type": "value_error"}],
        )
    if not wanted or len(wanted) > MAX_TRANSACTIONS_PER_INVOICE:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[{"loc": ["query", "ids"], "msg": f"between 1 and {MAX_TRANSACTIONS_PER_INVOICE} ids", "type": "value_error"}],
        )
    rows = db.execute(
        select(InvoiceTransaction.transaction_id, Invoice.id, Invoice.status, Invoice.number_text)
        .join(
            Invoice,
            and_(Invoice.organization_id == InvoiceTransaction.organization_id, Invoice.id == InvoiceTransaction.invoice_id),
        )
        .where(InvoiceTransaction.organization_id == ctx.organization_id, InvoiceTransaction.transaction_id.in_(wanted))
    )
    found = {tx_id: (invoice_id, state, number_text) for tx_id, invoice_id, state, number_text in rows}
    result = []
    for tx_id in wanted:
        invoice_id, state, number_text = found.get(tx_id, (None, None, None))
        label = "none" if state is None else ("invoiced" if state == InvoiceStatus.ISSUED else "draft")
        result.append(InvoiceStateRead(transaction_id=tx_id, state=label, invoice_id=invoice_id, number_text=number_text))
    return result


@router.post("", response_model=InvoiceRead, status_code=status.HTTP_201_CREATED)
def create_invoice(
    payload: InvoiceCreate,
    ctx: TenantContext = Depends(mutators),
    db: Session = Depends(get_db),
) -> InvoiceRead:
    invoice_id = service.create_draft(db, ctx, payload)
    return service.read_invoice(db, ctx, invoice_id)


# The invoice list's sort columns (amounts as issued; a draft has no number and sorts last by number).
INVOICE_SORTS = {
    "number": Invoice.number,
    "customer": func.lower(Invoice.customer_name),
    "invoice_date": Invoice.invoice_date,
    "due_date": Invoice.due_date,
    "status": Invoice.status,
    "currency": Invoice.currency,
    "net": Invoice.net_amount,
    "vat": Invoice.vat_amount,
    "gross": Invoice.gross_amount,
}


@router.get("", response_model=list[InvoiceSummary])
def list_invoices(
    status_filter: InvoiceStatus | None = Query(default=None, alias="status"),
    customer_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = Query(default=None, max_length=255, description="Customer name or invoice number contains"),
    payment: Literal["unpaid", "partially_paid", "paid", "open", "overdue", "not_yet_due", "refund_due"] | None = Query(
        default=None,
        description="Issued invoices by payment state (after credit notes); 'open' is anything not fully paid, 'overdue' "
        "open and past the due date, 'not_yet_due' open with the due date today or later (or none), 'refund_due' paid "
        "beyond what is owed",
    ),
    credit: Literal["credited", "partly_credited", "any"] | None = Query(default=None, description="Issued invoices with credit notes"),
    returns_filter: Literal["open", "follow_up_due"] | None = Query(
        default=None, alias="returns", description="Invoices with an open return case ('follow_up_due': its follow-up date has come)"
    ),
    paid_from: date | None = Query(default=None, description="Invoices with a payment dated on or after this day"),
    paid_to: date | None = Query(default=None, description="Invoices with a payment dated on or before this day"),
    page: Pagination = Depends(pagination),
    sort: Sorting = Depends(sorting),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[InvoiceSummary]:
    query = scoped_select(Invoice, ctx)
    if payment is not None:
        paid = payments.paid_sum_expression()
        owed = payments.owed_expression()
        query = query.where(Invoice.status == InvoiceStatus.ISSUED)
        if payment == "unpaid":
            query = query.where(paid <= 0, paid < owed)
        elif payment == "partially_paid":
            query = query.where(paid > 0, paid < owed)
        elif payment == "paid":
            query = query.where(paid >= owed)
        elif payment == "refund_due":
            query = query.where(paid > owed)
        else:
            query = query.where(paid < owed)
            if payment in ("overdue", "not_yet_due"):
                today = organization_today(db, ctx.organization_id)
                if payment == "overdue":
                    query = query.where(Invoice.due_date.is_not(None), Invoice.due_date < today)
                else:
                    query = query.where(or_(Invoice.due_date.is_(None), Invoice.due_date >= today))
    if paid_from is not None or paid_to is not None:
        # The dashboard's "Paid" figures: invoices that received a payment in the period (reversals are not payments).
        dated = [
            InvoicePayment.organization_id == Invoice.organization_id,
            InvoicePayment.invoice_id == Invoice.id,
            InvoicePayment.reverses_payment_id.is_(None),
            InvoicePayment.amount > 0,
        ]
        if paid_from is not None:
            dated.append(InvoicePayment.paid_on >= paid_from)
        if paid_to is not None:
            dated.append(InvoicePayment.paid_on <= paid_to)
        query = query.where(exists().where(*dated))
    if credit is not None:
        credited = credits.credited_sum_expression()
        query = query.where(Invoice.status == InvoiceStatus.ISSUED, credited > 0)
        if credit == "credited":
            query = query.where(credited >= Invoice.gross_amount)
        elif credit == "partly_credited":
            query = query.where(credited < Invoice.gross_amount)
    if returns_filter is not None:
        case = [InvoiceReturn.organization_id == Invoice.organization_id, InvoiceReturn.invoice_id == Invoice.id, InvoiceReturn.state.in_(OPEN_RETURN_STATES)]
        if returns_filter == "follow_up_due":
            case.append(InvoiceReturn.follow_up_on <= organization_today(db, ctx.organization_id))
        query = query.where(exists().where(*case))
    if status_filter is not None:
        query = query.where(Invoice.status == status_filter)
    if customer_id is not None:
        query = query.where(Invoice.customer_id == customer_id)
    if date_from is not None:
        query = query.where(Invoice.invoice_date >= date_from)
    if date_to is not None:
        query = query.where(Invoice.invoice_date <= date_to)
    if q:
        pattern = contains_pattern(q)
        query = query.where(or_(Invoice.customer_name.ilike(pattern, escape="\\"), Invoice.number_text.ilike(pattern, escape="\\")))
    invoices: Sequence[Invoice] = list(
        db.scalars(
            sorted_by(query, sort, INVOICE_SORTS, (Invoice.invoice_date.desc(), Invoice.created_at.desc(), Invoice.id)).limit(page.limit).offset(page.offset)
        )
    )
    counts = service.transaction_counts(db, ctx, [invoice.id for invoice in invoices])
    paid = payments.paid_amounts(db, ctx.organization_id, [invoice.id for invoice in invoices])
    credited = credits.credited_amounts(db, ctx.organization_id, [invoice.id for invoice in invoices])
    open_returns = returns.open_counts(db, ctx.organization_id, [invoice.id for invoice in invoices])
    return [
        InvoiceSummary(
            **service.summary_fields(invoice, counts.get(invoice.id, 0), paid.get(invoice.id, Decimal("0.00")), credited.get(invoice.id, Decimal("0.00"))),
            open_returns=open_returns.get(invoice.id, 0),
        )
        for invoice in invoices
    ]


# --- payments (recorded by hand; issued invoices only) -----------------------------------------------------------


@router.post("/{invoice_id}/payments", response_model=InvoiceRead, status_code=status.HTTP_201_CREATED)
def record_payment(invoice_id: uuid.UUID, payload: PaymentCreate, ctx: TenantContext = Depends(mutators), db: Session = Depends(get_db)) -> InvoiceRead:
    """Record a payment received. Never more than is outstanding; never dated in the future."""
    payments.record_payment(db, ctx, invoice_id, payload)
    return service.read_invoice(db, ctx, invoice_id)


@router.post("/{invoice_id}/payments/{payment_id}/reverse", response_model=InvoiceRead, status_code=status.HTTP_201_CREATED)
def reverse_payment(
    invoice_id: uuid.UUID, payment_id: uuid.UUID, payload: PaymentReversal, ctx: TenantContext = Depends(mutators), db: Session = Depends(get_db)
) -> InvoiceRead:
    """Undo a payment or refund recorded by mistake: a reversal row cancels it (nothing is deleted)."""
    payments.reverse_payment(db, ctx, invoice_id, payment_id, payload.note)
    return service.read_invoice(db, ctx, invoice_id)


@router.post("/{invoice_id}/refunds", response_model=InvoiceRead, status_code=status.HTTP_201_CREATED)
def record_refund(invoice_id: uuid.UUID, payload: RefundCreate, ctx: TenantContext = Depends(mutators), db: Session = Depends(get_db)) -> InvoiceRead:
    """Record money paid back to the customer. Never more than the refund due; never dated in the future."""
    payments.record_refund(db, ctx, invoice_id, payload)
    return service.read_invoice(db, ctx, invoice_id)


# --- credit notes (issued invoices only; numbered and final at once) -----------------------------------------------


@router.post("/{invoice_id}/credit-notes", response_model=CreditNoteRead, status_code=status.HTTP_201_CREATED)
def create_credit_note(
    invoice_id: uuid.UUID, payload: CreditNoteCreate, ctx: TenantContext = Depends(mutators), db: Session = Depends(get_db)
) -> CreditNoteRead:
    """Credit all or part of an issued invoice, line by line, at the invoice's own prices. Never more of a line than
    was invoiced and not credited yet; a reason is required."""
    credit_note_id = credits.create_credit_note(db, ctx, invoice_id, payload)
    return credits.read_credit_note(db, ctx, credit_note_id)


# --- return cases (the work before a credit note; closed by it or rejected) -----------------------------------------


@router.post("/{invoice_id}/returns", response_model=InvoiceRead, status_code=status.HTTP_201_CREATED)
def open_return(invoice_id: uuid.UUID, payload: ReturnCreate, ctx: TenantContext = Depends(mutators), db: Session = Depends(get_db)) -> InvoiceRead:
    """Open a return case: which lines and how many, why, and when to follow it up."""
    returns.open_return(db, ctx, invoice_id, payload)
    return service.read_invoice(db, ctx, invoice_id)


@router.post("/{invoice_id}/returns/{return_id}/goods-received", response_model=InvoiceRead)
def return_goods_received(
    invoice_id: uuid.UUID, return_id: uuid.UUID, payload: GoodsReceived, ctx: TenantContext = Depends(mutators), db: Session = Depends(get_db)
) -> InvoiceRead:
    returns.goods_received(db, ctx, invoice_id, return_id, payload.to_stock, payload.note)
    return service.read_invoice(db, ctx, invoice_id)


@router.post("/{invoice_id}/returns/{return_id}/approve", response_model=InvoiceRead)
def approve_return(
    invoice_id: uuid.UUID, return_id: uuid.UUID, payload: ReturnStep, ctx: TenantContext = Depends(mutators), db: Session = Depends(get_db)
) -> InvoiceRead:
    """To be credited: the credit note made for the case (with its `return_id`) closes it."""
    returns.approve(db, ctx, invoice_id, return_id, payload.note)
    return service.read_invoice(db, ctx, invoice_id)


@router.post("/{invoice_id}/returns/{return_id}/reject", response_model=InvoiceRead)
def reject_return(
    invoice_id: uuid.UUID, return_id: uuid.UUID, payload: ReturnRejection, ctx: TenantContext = Depends(mutators), db: Session = Depends(get_db)
) -> InvoiceRead:
    returns.reject(db, ctx, invoice_id, return_id, payload.reason)
    return service.read_invoice(db, ctx, invoice_id)


@router.post("/{invoice_id}/returns/{return_id}/notes", response_model=InvoiceRead, status_code=status.HTTP_201_CREATED)
def add_return_note(
    invoice_id: uuid.UUID, return_id: uuid.UUID, payload: ReturnNote, ctx: TenantContext = Depends(mutators), db: Session = Depends(get_db)
) -> InvoiceRead:
    returns.add_note(db, ctx, invoice_id, return_id, payload.note)
    return service.read_invoice(db, ctx, invoice_id)


@router.patch("/{invoice_id}/returns/{return_id}", response_model=InvoiceRead)
def set_return_follow_up(
    invoice_id: uuid.UUID, return_id: uuid.UUID, payload: ReturnFollowUp, ctx: TenantContext = Depends(mutators), db: Session = Depends(get_db)
) -> InvoiceRead:
    returns.set_follow_up(db, ctx, invoice_id, return_id, payload.follow_up_on)
    return service.read_invoice(db, ctx, invoice_id)


@router.get("/credit-notes/{credit_note_id}", response_model=CreditNoteRead)
def read_credit_note(credit_note_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)) -> CreditNoteRead:
    return credits.read_credit_note(db, ctx, credit_note_id)


@router.get(
    "/credit-notes/{credit_note_id}/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "The frozen PDF of a credit note"}},
)
def download_credit_note_pdf(credit_note_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)) -> Response:
    """The credit note's PDF ("Kreditfaktura"), frozen on first download like an invoice's."""
    return _pdf_response(pdf_service.get_or_create_credit_note_pdf(db, ctx, credit_note_id))


@router.get("/{invoice_id}", response_model=InvoiceRead)
def read_invoice(
    invoice_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> InvoiceRead:
    return service.read_invoice(db, ctx, invoice_id)


@router.get(
    "/{invoice_id}/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "The frozen PDF of an issued invoice"}},
)
def download_invoice_pdf(
    invoice_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> Response:
    """The PDF of an issued invoice. Any member who can read the invoice may download it.

    The first request renders it from the stored invoice and stores the bytes; every later request
    returns exactly those bytes (see pdf/service.py). A draft has no PDF.
    """
    return _pdf_response(pdf_service.get_or_create_pdf(db, ctx, invoice_id))


def _pdf_response(stored) -> Response:
    return Response(
        content=stored.content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(stored.filename),
            "Content-Length": str(len(stored.content)),
            "ETag": f'"{stored.sha256}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.patch("/{invoice_id}", response_model=InvoiceRead)
def update_invoice(
    invoice_id: uuid.UUID,
    payload: InvoiceUpdate,
    if_match: str | None = Header(default=None),
    ctx: TenantContext = Depends(mutators),
    db: Session = Depends(get_db),
) -> InvoiceRead:
    service.update_draft(db, ctx, invoice_id, payload.model_dump(exclude_unset=True), if_match)
    return service.read_invoice(db, ctx, invoice_id)


@router.post("/{invoice_id}/issue", response_model=InvoiceRead)
def issue_invoice(
    invoice_id: uuid.UUID,
    if_match: str | None = Header(default=None),
    ctx: TenantContext = Depends(mutators),
    db: Session = Depends(get_db),
) -> InvoiceRead:
    service.issue(db, ctx, invoice_id, if_match)
    return service.read_invoice(db, ctx, invoice_id)


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_invoice(
    invoice_id: uuid.UUID,
    if_match: str | None = Header(default=None),
    ctx: TenantContext = Depends(mutators),
    db: Session = Depends(get_db),
) -> Response:
    service.delete_draft(db, ctx, invoice_id, if_match)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
