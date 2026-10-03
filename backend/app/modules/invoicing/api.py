import uuid
from collections.abc import Sequence
from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from app.api.deps import Pagination, pagination
from app.core.authz import roles_required
from app.core.db import get_db
from app.core.query import contains_pattern
from app.core.tenant import TenantContext, get_tenant_context
from app.core.tenant_scope import scoped_select
from app.models import Customer, Role
from app.modules.invoicing import service
from app.modules.invoicing.models import Invoice, InvoiceStatus, InvoiceTransaction
from app.modules.invoicing.schemas import (
    MAX_TRANSACTIONS_PER_INVOICE,
    InvoiceableTotals,
    InvoiceableTransaction,
    InvoiceCreate,
    InvoiceRead,
    InvoiceStateRead,
    InvoiceSummary,
    InvoiceUpdate,
)
from app.modules.sales.models import Transaction, TransactionLine, TransactionStatus
from app.modules.sales.pricing import calculate_totals
from app.schemas.customer import CustomerRef

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
    page: Pagination = Depends(pagination),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[InvoiceSummary]:
    query = scoped_select(Invoice, ctx)
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
    return [InvoiceSummary(**service.summary_fields(invoice, counts.get(invoice.id, 0))) for invoice in invoices]


@router.get("/{invoice_id}", response_model=InvoiceRead)
def read_invoice(
    invoice_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> InvoiceRead:
    return service.read_invoice(db, ctx, invoice_id)


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
