import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import Pagination, pagination
from app.core.authz import roles_required
from app.core.db import get_db
from app.core.org_time import MONTH_PATTERN, month_range, organization_today
from app.core.query import contains_pattern
from app.core.tenant import TenantContext, get_tenant_context
from app.core.tenant_scope import scoped_select
from app.models import Customer, Role
from app.modules.invoicing import payments, service
from app.modules.invoicing.pdf import service as pdf_service
from app.modules.invoicing.pdf.filename import content_disposition
from app.modules.invoicing.models import Invoice, InvoicePayment, InvoiceStatus, InvoiceTransaction
from app.modules.invoicing.schemas import (
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


@invoiceable_router.get("", response_model=list[InvoiceableTransaction])
def list_invoiceable_transactions(
    customer_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: Pagination = Depends(pagination),
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
    rows = db.execute(
        query.order_by(Transaction.transaction_date.desc(), Transaction.created_at.desc(), Transaction.id)
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
        amounts = db.execute(select(Invoice.currency, func.sum(Invoice.gross_amount)).where(*where).group_by(Invoice.currency).order_by(Invoice.currency)).all()
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
        paid_this_month=_paid_between(db, ctx, month_start, month_end),
    )


def _outstanding(db: Session, ctx: TenantContext, *conditions) -> CountAndAmounts:
    """Issued invoices not fully paid (and matching `conditions`); the amounts are what is still outstanding."""
    paid = payments.paid_sum_expression()
    where = (
        Invoice.organization_id == ctx.organization_id,
        Invoice.status == InvoiceStatus.ISSUED,
        paid < Invoice.gross_amount,
        *conditions,
    )
    count = db.scalar(select(func.count()).select_from(Invoice).where(*where))
    amounts = db.execute(select(Invoice.currency, func.sum(Invoice.gross_amount - paid)).where(*where).group_by(Invoice.currency).order_by(Invoice.currency)).all()
    return CountAndAmounts(count=count, amounts=[CurrencyAmount(currency=c, amount=a) for c, a in amounts])


def _paid_between(db: Session, ctx: TenantContext, start: date, end: date) -> CountAndAmounts:
    """Payments dated in the period, per currency (reversals subtracted); the count is of payments, not reversals."""
    where = (
        InvoicePayment.organization_id == ctx.organization_id,
        InvoicePayment.paid_on >= start,
        InvoicePayment.paid_on <= end,
    )
    joined = and_(Invoice.organization_id == InvoicePayment.organization_id, Invoice.id == InvoicePayment.invoice_id)
    count = db.scalar(select(func.count()).select_from(InvoicePayment).where(*where, InvoicePayment.reverses_payment_id.is_(None)))
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


@router.get("", response_model=list[InvoiceSummary])
def list_invoices(
    status_filter: InvoiceStatus | None = Query(default=None, alias="status"),
    customer_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = Query(default=None, max_length=255, description="Customer name or invoice number contains"),
    payment: Literal["unpaid", "partially_paid", "paid", "open"] | None = Query(
        default=None, description="Issued invoices by payment state; 'open' is anything not fully paid"
    ),
    page: Pagination = Depends(pagination),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[InvoiceSummary]:
    query = scoped_select(Invoice, ctx)
    if payment is not None:
        paid = payments.paid_sum_expression()
        query = query.where(Invoice.status == InvoiceStatus.ISSUED)
        if payment == "unpaid":
            query = query.where(paid <= 0)
        elif payment == "partially_paid":
            query = query.where(paid > 0, paid < Invoice.gross_amount)
        elif payment == "paid":
            query = query.where(paid >= Invoice.gross_amount)
        else:
            query = query.where(paid < Invoice.gross_amount)
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
            query.order_by(Invoice.invoice_date.desc(), Invoice.created_at.desc(), Invoice.id).limit(page.limit).offset(page.offset)
        )
    )
    counts = service.transaction_counts(db, ctx, [invoice.id for invoice in invoices])
    paid = payments.paid_amounts(db, ctx.organization_id, [invoice.id for invoice in invoices])
    return [
        InvoiceSummary(**service.summary_fields(invoice, counts.get(invoice.id, 0), paid.get(invoice.id, Decimal("0.00")))) for invoice in invoices
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
    """Undo a payment recorded by mistake: a reversal row cancels it (nothing is deleted)."""
    payments.reverse_payment(db, ctx, invoice_id, payment_id, payload.note)
    return service.read_invoice(db, ctx, invoice_id)


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
    stored = pdf_service.get_or_create_pdf(db, ctx, invoice_id)
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
