"""Creating, reading, issuing and deleting invoices.

Locking, in one rule: every writer that can change a Sales transaction first locks its row
(`SELECT ... FOR UPDATE`). Invoicing takes the same locks, in a fixed order, so whatever it
reads under them cannot change before it commits.

    create   locks the source TRANSACTIONS (ordered by id), reads, inserts the new invoice
    issue    locks the INVOICE, then its source transactions (ordered by id), re-verifies
    delete   locks the INVOICE (the deletion releases the reservation)
    reopen / cancel (Sales)  lock the transaction and then ask the lifecycle seam, whose
                             Invoicing validator reads invoice_transactions without locking it

No path holds a transaction lock while waiting for an invoice lock, so there is no cycle. Two
orderings of "create" against "reopen" are both consistent: reopen first, so creation sees a
draft and refuses; creation first, so the validator sees the link and refuses the reopen.

Invoicing by delivery (2026-10-11): what is invoiced is what was delivered (see delivery.py). Delivered
quantities only grow while an order is completed (a reopen or cancel, which would take deliveries back,
is refused while the order is on an invoice), so reading them under the transaction lock is enough:
a delivery that commits later is invoiced later.
"""

import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import audit, subjects
from app.core.entity_registry import registry
from app.core.org_time import organization_today, organization_zone
from app.core.tenant import TenantContext
from app.core.tenant_scope import create_scoped, get_scoped, get_scoped_or_404, reference_error, scoped_select
from app.models import Customer, Organization, User
from app.modules.custom_fields import service as custom_fields
from app.modules.invoicing import delivery, numbering, snapshots
from app.modules.invoicing import credits, payments, returns
from app.modules.invoicing.models import (
    Invoice,
    InvoiceHold,
    InvoiceLine,
    InvoiceStatus,
    InvoiceTransaction,
    InvoiceVatRow,
)
from app.modules.invoicing.schemas import (
    MAX_TRANSACTIONS_PER_INVOICE,
    InvoiceCreate,
    InvoiceLineRead,
    InvoiceRead,
    InvoiceTransactionRead,
    VatRowRead,
)
from app.modules.sales.models import Transaction, TransactionLine, TransactionStatus
from app.modules.sales.pricing import calculate_line, calculate_totals
from app.core.versioning import ensure_current

# Registry keys of the entities whose custom fields are flagged for invoices.
TRANSACTION = "transaction"
TRANSACTION_LINE = "transaction_line"
INVOICE_FLAG = "show_on_invoice"

WITHIN_ORDER_TRIGGER = "invoice_lines_within_order"
ZERO = Decimal("0.00")


# --- structured refusals -------------------------------------------------------------------------------------


# What an invoice's history records: the header a person sees and decides on. The frozen customer/issuer
# snapshots and the lines are documents of their own, kept unchanged in the invoice itself.
AUDITED_FIELDS = (
    "status", "number_text", "customer_name", "currency", "invoice_date", "due_date", "description",
    "net_amount", "vat_amount", "gross_amount", "issued_at",
)

def conflict(code: str, message: str, transaction_ids: Sequence[uuid.UUID] = ()) -> HTTPException:
    """A 409 with a machine-readable code and, where relevant, the (own) transactions concerned."""
    detail: dict[str, Any] = {"code": code, "message": message}
    if transaction_ids:
        detail["transaction_ids"] = [str(transaction_id) for transaction_id in transaction_ids]
    return HTTPException(status.HTTP_409_CONFLICT, detail=detail)


# --- totals --------------------------------------------------------------------------------------------------


def sum_lines(lines: Sequence[Any]) -> tuple[Decimal, Decimal, Decimal, dict[Decimal, list[Decimal]]]:
    """Net, VAT and gross as plain sums of the STORED line amounts, and the same sums per VAT
    rate. Nothing is recalculated or re-rounded: the lines already add up."""
    net = vat = gross = ZERO
    by_rate: dict[Decimal, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    for line in lines:
        net += line.net_amount
        vat += line.vat_amount
        gross += line.gross_amount
        row = by_rate[line.vat_rate]
        row[0] += line.net_amount
        row[1] += line.vat_amount
    return net, vat, gross, by_rate


# --- creating a draft ----------------------------------------------------------------------------------------


def _on_a_draft(db: Session, ctx: TenantContext, transaction_ids: Sequence[uuid.UUID]) -> list[uuid.UUID]:
    """The orders among `transaction_ids` that are on a DRAFT invoice (an order has at most one draft at a time)."""
    return list(
        db.scalars(
            select(InvoiceTransaction.transaction_id)
            .join(Invoice, (Invoice.organization_id == InvoiceTransaction.organization_id) & (Invoice.id == InvoiceTransaction.invoice_id))
            .where(
                InvoiceTransaction.organization_id == ctx.organization_id,
                InvoiceTransaction.transaction_id.in_(list(transaction_ids)),
                Invoice.status == InvoiceStatus.DRAFT,
            )
            .order_by(InvoiceTransaction.transaction_id)
        )
    )


def _fresh(query):
    """Re-read rows from the database even if this session already holds them. What is verified
    under a lock must be what the database has now, not what the session happened to remember
    (a request normally has a fresh session; this makes the guarantee independent of that)."""
    return query.execution_options(populate_existing=True)


def _lock_sources(db: Session, ctx: TenantContext, transaction_ids: Sequence[uuid.UUID]) -> list[Transaction]:
    """The transactions of THIS organization among `transaction_ids`, locked in id order."""
    return list(
        db.scalars(
            _fresh(
                scoped_select(Transaction, ctx)
                .where(Transaction.id.in_(list(transaction_ids)))
                .order_by(Transaction.id)
                .with_for_update()
            )
        )
    )


def _check_invoiceable(db: Session, ctx: TenantContext, sources: Sequence[Transaction]) -> None:
    """The rules of what may share an invoice, checked under the locks."""
    ids = [t.id for t in sources]
    not_completed = [t.id for t in sources if t.status != TransactionStatus.COMPLETED]
    if not_completed:
        raise conflict("transactions_not_completed", "Only completed orders can be invoiced", not_completed)
    paid = [t.id for t in sources if t.paid_at is not None]
    if paid:
        raise conflict("paid_at_counter", "An order paid at the counter has a receipt and is never invoiced", paid)
    if len({t.billing_customer_id for t in sources}) > 1:
        raise conflict("mixed_customers", "All orders on an invoice must have the same billing customer", ids)
    walk_in = db.scalar(
        select(Customer.id).where(Customer.organization_id == ctx.organization_id, Customer.id == sources[0].billing_customer_id, Customer.walk_in.is_(True))
    )
    if walk_in is not None:
        raise conflict("walk_in_customer", "An invoice needs a named customer; the walk-in customer pays at the counter", ids)
    without_currency = [t.id for t in sources if t.currency is None]
    if without_currency:
        raise conflict(
            "currency_missing",
            "An order without a currency cannot be invoiced; an owner or admin can assign the "
            "organization's currency to earlier orders in the settings",
            without_currency,
        )
    if len({t.currency for t in sources}) > 1:
        raise conflict("mixed_currencies", "All orders on an invoice must be in the same currency", ids)
    reserved = _on_a_draft(db, ctx, ids)
    if reserved:
        raise conflict("already_invoiced", "An order is already on a draft invoice", reserved)


def _first_by_transaction(rows: Sequence[Any], key: str = "transaction_id") -> dict[uuid.UUID, list[Any]]:
    grouped: dict[uuid.UUID, list[Any]] = defaultdict(list)
    for row in rows:
        grouped[getattr(row, key)].append(row)
    return grouped


def create_draft(db: Session, ctx: TenantContext, payload: InvoiceCreate) -> uuid.UUID:
    """Reserve the given completed transactions on a new draft invoice. Commits."""
    requested = list(payload.transaction_ids)

    # 3-4. Lock in deterministic order. Foreign and random ids are simply not found, and a
    # mixture of found and not found gives the one answer: the caller learns nothing about which.
    sources = _lock_sources(db, ctx, requested)
    if len(sources) != len(requested):
        reference_error("transaction_ids", "One or more orders were not found", "reference.not_found")

    # 5-8. Eligibility, under the locks.
    _check_invoiceable(db, ctx, sources)

    invoice_date = payload.invoice_date or organization_today(db, ctx.organization_id)
    if payload.due_date is not None and payload.due_date < invoice_date:
        reference_error("due_date", "The due date cannot be before the invoice date", "value_error")
    terms = db.scalar(select(Organization.payment_terms_days).where(Organization.id == ctx.organization_id))
    # Without a due date, the organization's payment terms decide it (none set: no due date, as before).
    due_date = payload.due_date if payload.due_date is not None or terms is None else invoice_date + timedelta(days=terms)

    # 9. Everything that is read, is read while the transaction locks are held. Nothing a Sales
    # writer can touch (lines, header, status, custom values) can change underneath us.
    ordered = sorted(sources, key=lambda t: (t.transaction_date, t.created_at, t.id))
    source_lines = list(
        db.scalars(
            _fresh(
                scoped_select(TransactionLine, ctx)
                .where(TransactionLine.transaction_id.in_([t.id for t in ordered]))
                .order_by(TransactionLine.position, TransactionLine.id)
            )
        )
    )
    lines_of = _first_by_transaction(source_lines)
    customer = get_scoped(db, ctx, Customer, ordered[0].billing_customer_id)
    organization = db.scalar(select(Organization).where(Organization.id == ctx.organization_id))
    assert customer is not None and organization is not None  # guaranteed by the composite foreign keys
    transaction_fields = custom_fields.read_values(db, ctx, TRANSACTION, [t.id for t in ordered], flag=INVOICE_FLAG)
    line_fields = custom_fields.read_values(db, ctx, TRANSACTION_LINE, [l.id for l in source_lines], flag=INVOICE_FLAG)

    # Invoicing by delivery: only what was delivered and is not invoiced yet goes on the invoice.
    parts_of = delivery.parts(db, ctx, ordered, lines_of)
    nothing = [t.id for t in ordered if not parts_of.get(t.id)]
    if nothing:
        raise conflict(
            "nothing_to_invoice",
            "Nothing of this order can be invoiced yet: nothing more has been delivered, or it waits until everything is delivered",
            nothing,
        )
    flat_parts = [part for t in ordered for part in parts_of[t.id]]
    complete = delivery.completes(db, ctx, ordered, lines_of, parts_of)

    # 13. Totals are plain sums of the stored amounts; for whole lines, Sales' own totals must agree.
    net, vat, gross, by_rate = sum_lines(flat_parts)
    if all(part.whole for part in flat_parts):
        sales_totals = calculate_totals([part.line for part in flat_parts])
        if (net, vat, gross) != (sales_totals.net_amount, sales_totals.vat_amount, sales_totals.gross_amount):
            raise RuntimeError("invoice totals disagree with the Sales totals of the same lines")  # never store this

    snapshot = snapshots.customer_snapshot(customer)
    try:
        with db.begin_nested():  # all or nothing, also when the surrounding session is shared
            invoice = create_scoped(
                db,
                ctx,
                Invoice,
                customer_id=customer.id,
                currency=ordered[0].currency,
                customer_snapshot=snapshot,
                issuer_snapshot=snapshots.issuer_snapshot(organization, ctx.user.name),
                customer_name=customer.name,
                invoice_date=invoice_date,
                due_date=due_date,
                description=payload.description,
                net_amount=net,
                vat_amount=vat,
                gross_amount=gross,
            )
            _insert_children(db, ctx, invoice, ordered, parts_of, complete, transaction_fields, line_fields, by_rate)
            audit.created(db, ctx, invoice, "invoice", fields=AUDITED_FIELDS)
    except IntegrityError as error:
        # 10. The database's last guard: never more of an order line, over all invoices, than was ordered.
        if _violated(error) == WITHIN_ORDER_TRIGGER:
            raise conflict("already_invoiced", "An order is already invoiced", requested) from error
        raise
    invoice_id = invoice.id
    db.commit()  # 14
    return invoice_id


def _has_invoice(db: Session, ctx: TenantContext, transaction_id: uuid.UUID) -> bool:
    return (
        db.scalar(
            select(InvoiceTransaction.id).where(InvoiceTransaction.organization_id == ctx.organization_id, InvoiceTransaction.transaction_id == transaction_id).limit(1)
        )
        is not None
    )


def _partial_delivery(order: Transaction, short: list[tuple[TransactionLine, Decimal, Decimal]]) -> HTTPException:
    """Not everything is delivered and nobody has chosen yet: the person decides (owner, 2026-10-11)."""
    return HTTPException(
        status.HTTP_409_CONFLICT,
        detail={
            "code": "partial_delivery",
            "message": "Not everything on this order has been delivered. Invoice what is delivered now, or wait until everything is delivered?",
            "transaction_ids": [str(order.id)],
            "lines": [
                {"description": line.description, "unit": line.unit, "ordered": f"{line.quantity:.3f}", "delivered": f"{handed:.3f}", "backordered": f"{left:.3f}"}
                for line, handed, left in short
            ],
        },
    )


def invoice_order(db: Session, ctx: TenantContext, transaction_id: uuid.UUID, *, partial: bool = False) -> uuid.UUID:
    """Put a completed order on an invoice ("Invoice" on the order, 2026-10-10): onto the customer's open draft in the
    same currency, so a customer's orders can be collected and issued together, or onto a new draft. Commits.

    A draft's sources never change in place (its lines, totals and VAT rows are built from them once), so the open
    draft is rebuilt: deleted and created again with its orders plus this one, keeping its dates and description, in
    this one database transaction. If anything is refused, the old draft stays as it was.

    Invoicing by delivery (2026-10-11): only what is delivered is invoiced. The first time an order that is not
    delivered in full is invoiced, the person chooses: `partial` (invoice the delivered part now) or a hold (wait
    until everything is delivered, see `hold`). Without `partial` that question is the answer (409 partial_delivery).
    """
    order = get_scoped(db, ctx, Transaction, transaction_id)
    if order is None:
        reference_error("transaction_id", "Order not found", "reference.not_found")
    if order.status == TransactionStatus.COMPLETED:
        lines = list(db.scalars(scoped_select(TransactionLine, ctx).where(TransactionLine.transaction_id == order.id)))
        short = delivery.shortage(db, ctx, lines)
        waiting = order.id in delivery.held(db, ctx, [order.id])
        if short and not partial:
            if waiting:
                raise conflict("waiting_for_delivery", "This order waits until everything is delivered", [order.id])
            if not _has_invoice(db, ctx, order.id):
                raise _partial_delivery(order, short)
        if partial and waiting:
            db.execute(delete(InvoiceHold).where(InvoiceHold.organization_id == ctx.organization_id, InvoiceHold.transaction_id == order.id))
    draft = db.scalar(
        scoped_select(Invoice, ctx)
        .where(Invoice.status == InvoiceStatus.DRAFT, Invoice.customer_id == order.billing_customer_id, Invoice.currency == order.currency)
        .order_by(Invoice.created_at.desc(), Invoice.id)
        .limit(1)
        .with_for_update()
    )
    sources = (
        list(
            db.scalars(
                select(InvoiceTransaction.transaction_id).where(
                    InvoiceTransaction.organization_id == ctx.organization_id, InvoiceTransaction.invoice_id == draft.id
                )
            )
        )
        if draft is not None
        else []
    )
    if draft is None or (len(sources) >= MAX_TRANSACTIONS_PER_INVOICE and transaction_id not in sources):
        return create_draft(db, ctx, InvoiceCreate(transaction_ids=[transaction_id]))
    # An order already on the draft is rebuilt with it: what was delivered since is added.
    wanted = [*sources, transaction_id] if transaction_id not in sources else list(sources)
    header = InvoiceCreate(transaction_ids=wanted, invoice_date=draft.invoice_date, due_date=draft.due_date, description=draft.description)
    audit.deleted(db, ctx, draft, "invoice", fields=AUDITED_FIELDS)
    db.delete(draft)
    db.flush()
    return create_draft(db, ctx, header)


def hold(db: Session, ctx: TenantContext, transaction_id: uuid.UUID) -> None:
    """"Wait until everything is delivered" for a completed order: nothing of it is invoiced before then. Commits."""
    order = db.scalar(_fresh(scoped_select(Transaction, ctx).where(Transaction.id == transaction_id).with_for_update()))
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    if order.status != TransactionStatus.COMPLETED or order.paid_at is not None:
        raise conflict("not_invoiceable", "Only a completed order that is invoiced can wait for its delivery", [order.id])
    if _on_a_draft(db, ctx, [order.id]):
        raise conflict("already_invoiced", "The order is on a draft invoice; delete the draft first", [order.id])
    if not delivery.held(db, ctx, [order.id]):
        create_scoped(db, ctx, InvoiceHold, transaction_id=order.id, created_by=ctx.user.id, updated_by=ctx.user.id)
    db.commit()


def release(db: Session, ctx: TenantContext, transaction_id: uuid.UUID) -> None:
    """Stop waiting: the order is invoiced by delivery again (nothing is invoiced by this). Commits."""
    order = get_scoped(db, ctx, Transaction, transaction_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    db.execute(delete(InvoiceHold).where(InvoiceHold.organization_id == ctx.organization_id, InvoiceHold.transaction_id == order.id))
    db.commit()


INVOICING_ROLES = ("owner", "admin", "accountant")


def invoice_deliveries(db: Session, ctx: TenantContext, transaction_ids: Sequence[uuid.UUID]) -> list[uuid.UUID]:
    """After backordered units are delivered (owner, 2026-10-11): they go onto the customer's open draft invoice, for
    every order that is invoiced by delivery (it is on an invoice already) and every order that waited and is now
    delivered in full. An order nobody has invoiced yet waits in "Ready to invoice" (nobody chose for it), and so does
    everything when the person delivering may not make invoices. Each order is its own step; one refusal (a draft that
    is full, a currency missing) leaves that order in "Ready to invoice" and the others are still invoiced."""
    if ctx.role not in INVOICING_ROLES:
        return []
    drafts: list[uuid.UUID] = []
    for transaction_id in dict.fromkeys(transaction_ids):
        if not (_has_invoice(db, ctx, transaction_id) or delivery.held(db, ctx, [transaction_id])):
            continue
        try:
            drafts.append(invoice_order(db, ctx, transaction_id))
        except HTTPException:
            db.rollback()
    return list(dict.fromkeys(drafts))


def _violated(error: IntegrityError) -> str | None:
    diag = getattr(error.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


def _service_snapshots(db: Session, ctx: TenantContext, lines: list[TransactionLine]) -> dict[uuid.UUID, dict[str, Any]]:
    """What an invoice shows of each service line, as it reads at invoicing (never resolved again): when, by whom
    and for whom, in words. The subject's label comes from the registry (Invoicing does not know what it is)."""
    service_lines = [line for line in lines if line.kind == "service"]
    if not service_lines:
        return {}
    labels = subjects.subject_labels(db, ctx.organization_id, {(line.subject_type, line.subject_id) for line in service_lines})
    performers = {line.performed_by for line in service_lines if line.performed_by is not None}
    names = dict(db.execute(select(User.id, User.name).where(User.id.in_(performers))).all()) if performers else {}
    zone = organization_zone(db, ctx.organization_id)
    return {
        line.id: {
            "schema": 1,
            "performed_at": line.performed_at.isoformat(),
            # As a person in the organization reads it, so the document never needs a time zone to be shown.
            "performed_at_local": line.performed_at.astimezone(zone).strftime("%Y-%m-%d %H:%M"),
            "performed_by": names.get(line.performed_by),
            "subject_type": line.subject_type,
            "subject_label": labels.get((line.subject_type, line.subject_id)),
            "notes": line.notes,
        }
        for line in service_lines
    }


def _insert_children(
    db: Session,
    ctx: TenantContext,
    invoice: Invoice,
    ordered: Sequence[Transaction],
    parts_of: dict[uuid.UUID, list[delivery.Part]],
    complete: dict[uuid.UUID, bool],
    transaction_fields: dict[uuid.UUID, list[Any]],
    line_fields: dict[uuid.UUID, list[Any]],
    by_rate: dict[Decimal, list[Decimal]],
) -> None:
    """Sources, lines (whole lines copied verbatim, delivered parts priced the same way) and the stored VAT
    breakdown of a new invoice. Only orders with something to invoice are sources."""
    services = _service_snapshots(db, ctx, [part.line for parts in parts_of.values() for part in parts])
    position = 0
    for index, source in enumerate([t for t in ordered if parts_of.get(t.id)], start=1):
        # The unique key on the source is checked here, when the link row is flushed.
        create_scoped(
            db,
            ctx,
            InvoiceTransaction,
            invoice_id=invoice.id,
            transaction_id=source.id,
            customer_id=invoice.customer_id,
            currency=invoice.currency,
            transaction_date=source.transaction_date,
            transaction_number=source.number,
            source_version=source.version,
            position=index,
            fields=snapshots.custom_field_snapshot(transaction_fields.get(source.id, [])),
            completes_order=complete[source.id],
        )
        for part in parts_of[source.id]:
            line = part.line
            position += 1
            create_scoped(
                db,
                ctx,
                InvoiceLine,
                invoice_id=invoice.id,
                source_transaction_id=source.id,
                source_line_id=line.id,
                position=position,
                description=line.description,
                unit=line.unit,
                quantity=part.quantity,
                unit_price_ex_vat=line.unit_price_ex_vat,
                list_unit_price=line.list_unit_price,
                catalog_discount_percent=line.catalog_discount_percent,
                customer_discount_percent=line.customer_discount_percent,
                line_discount_percent=line.line_discount_percent,
                vat_rate=line.vat_rate,
                net_amount=part.net_amount,
                vat_amount=part.vat_amount,
                gross_amount=part.gross_amount,
                fields=snapshots.custom_field_snapshot(line_fields.get(line.id, [])),
                service=services.get(line.id),
                ordered_quantity=part.ordered_quantity,
                backordered_quantity=part.backordered_quantity,
            )
    for rate in sorted(by_rate):
        net, vat = by_rate[rate]
        create_scoped(db, ctx, InvoiceVatRow, invoice_id=invoice.id, vat_rate=rate, net_amount=net, vat_amount=vat)


# --- reading -------------------------------------------------------------------------------------------------


def read_invoice(db: Session, ctx: TenantContext, invoice_id: uuid.UUID) -> InvoiceRead:
    """The stored document. Reads the invoicing tables and nothing else."""
    invoice = get_scoped_or_404(db, ctx, Invoice, invoice_id)
    transactions = list(
        db.scalars(
            scoped_select(InvoiceTransaction, ctx)
            .where(InvoiceTransaction.invoice_id == invoice.id)
            .order_by(InvoiceTransaction.position)
        )
    )
    lines = list(
        db.scalars(scoped_select(InvoiceLine, ctx).where(InvoiceLine.invoice_id == invoice.id).order_by(InvoiceLine.position))
    )
    vat_rows = list(
        db.scalars(
            scoped_select(InvoiceVatRow, ctx).where(InvoiceVatRow.invoice_id == invoice.id).order_by(InvoiceVatRow.vat_rate)
        )
    )
    paid = payments.paid_amounts(db, ctx.organization_id, [invoice.id]).get(invoice.id, Decimal("0.00"))
    credited = credits.credited_amounts(db, ctx.organization_id, [invoice.id]).get(invoice.id, Decimal("0.00"))
    refunded = payments.refunded_amounts(db, ctx.organization_id, [invoice.id]).get(invoice.id, Decimal("0.00"))
    credited_lines = credits.credited_quantities(db, ctx.organization_id, [row.id for row in lines])
    returnable = {}
    if invoice.status == InvoiceStatus.ISSUED:
        for answer in registry.call_hooks("stock.returnable", db, ctx.organization_id, [row.source_line_id for row in lines]):
            returnable.update(answer)
    return InvoiceRead(
        **summary_fields(invoice, len(transactions), paid, credited, refunded),
        payments=payments.list_payments(db, ctx, invoice.id),
        credit_notes=credits.credit_note_summaries(db, ctx, invoice.id),
        returns=(cases := returns.list_returns(db, ctx, invoice.id)),
        open_returns=sum(1 for case in cases if case.state in ("requested", "goods_received", "approved")),
        issued_by=invoice.issued_by,
        created_by=invoice.created_by,
        updated_by=invoice.updated_by,
        customer_snapshot=invoice.customer_snapshot,
        issuer_snapshot=invoice.issuer_snapshot,
        transactions=[InvoiceTransactionRead.model_validate(row) for row in transactions],
        lines=[
            InvoiceLineRead.model_validate(row).model_copy(
                update={
                    "credited_quantity": credited_lines.get(row.id, Decimal("0.000")),
                    "creditable_quantity": row.quantity - credited_lines.get(row.id, Decimal("0.000")),
                    # Per order line from Inventory; an order line can be on several invoices (invoicing by delivery),
                    # so this line can return at most what is left to credit of it.
                    "stock_returnable": (
                        None
                        if returnable.get(row.source_line_id) is None
                        else min(returnable[row.source_line_id], row.quantity - credited_lines.get(row.id, Decimal("0.000")))
                    ),
                }
            )
            for row in lines
        ],
        vat_breakdown=[VatRowRead.model_validate(row) for row in vat_rows],
    )


def summary_fields(
    invoice: Invoice, transaction_count: int, paid: Decimal = Decimal("0.00"), credited: Decimal = Decimal("0.00"), refunded: Decimal = Decimal("0.00")
) -> dict[str, Any]:
    return dict(
        **payments.payment_fields(invoice, paid, credited, refunded),
        id=invoice.id,
        status=invoice.status,
        version=invoice.version,
        series=invoice.series,
        number=invoice.number,
        number_text=invoice.number_text,
        customer_id=invoice.customer_id,
        customer_name=invoice.customer_name,
        currency=invoice.currency,
        invoice_date=invoice.invoice_date,
        due_date=invoice.due_date,
        description=invoice.description,
        net_amount=invoice.net_amount,
        vat_amount=invoice.vat_amount,
        gross_amount=invoice.gross_amount,
        transaction_count=transaction_count,
        issued_at=invoice.issued_at,
        created_at=invoice.created_at,
        updated_at=invoice.updated_at,
    )


def transaction_counts(db: Session, ctx: TenantContext, invoice_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not invoice_ids:
        return {}
    rows = db.execute(
        select(InvoiceTransaction.invoice_id, func.count())
        .where(InvoiceTransaction.organization_id == ctx.organization_id, InvoiceTransaction.invoice_id.in_(list(invoice_ids)))
        .group_by(InvoiceTransaction.invoice_id)
    )
    return {invoice_id: count for invoice_id, count in rows}


# --- draft header ----------------------------------------------------------------------------------------------


def require_draft(invoice: Invoice, what: str) -> None:
    if invoice.status != InvoiceStatus.DRAFT:
        raise conflict("invoice_issued", f"An issued invoice cannot be {what}")


def update_draft(db: Session, ctx: TenantContext, invoice_id: uuid.UUID, values: dict[str, Any], if_match: str | None) -> None:
    invoice = get_scoped_or_404(db, ctx, Invoice, invoice_id, for_update=True)
    require_draft(invoice, "changed")
    ensure_current(if_match, invoice.version, "invoice", invoice.id)
    final_date = values.get("invoice_date", invoice.invoice_date)
    final_due = values.get("due_date", invoice.due_date)
    if final_due is not None and final_due < final_date:
        reference_error("due_date", "The due date cannot be before the invoice date", "value_error")
    if any(getattr(invoice, field) != value for field, value in values.items()):  # a no-op moves no version
        before = audit.snapshot(invoice, AUDITED_FIELDS)
        for field, value in values.items():
            setattr(invoice, field, value)
        invoice.version += 1
        audit.updated(db, ctx, invoice, "invoice", before, fields=AUDITED_FIELDS)
    db.commit()


def delete_draft(db: Session, ctx: TenantContext, invoice_id: uuid.UUID, if_match: str | None) -> None:
    """Delete a draft; its sources, lines and VAT rows go with it, which releases the reservation."""
    invoice = get_scoped_or_404(db, ctx, Invoice, invoice_id, for_update=True)
    require_draft(invoice, "deleted")
    ensure_current(if_match, invoice.version, "invoice", invoice.id)
    audit.deleted(db, ctx, invoice, "invoice", fields=AUDITED_FIELDS)
    db.delete(invoice)
    db.commit()


# --- issuing -------------------------------------------------------------------------------------------------------


def _source_changed(message: str) -> HTTPException:
    return conflict(
        "source_changed",
        f"{message}. The draft no longer matches its source orders and cannot be issued; delete it and create a new one",
    )


def _verify_unchanged(
    db: Session,
    ctx: TenantContext,
    invoice: Invoice,
    links: Sequence[InvoiceTransaction],
    sources: Sequence[Transaction],
    lines: Sequence[InvoiceLine],
) -> list[TransactionLine]:
    """Nothing the draft reserved may differ from what it copied. Returns the source lines."""
    if {s.id for s in sources} != {link.transaction_id for link in links} or len(sources) != len(links):
        raise _source_changed("A source order is missing")
    by_id = {s.id: s for s in sources}
    for link in links:
        source = by_id[link.transaction_id]
        if source.status != TransactionStatus.COMPLETED:
            raise _source_changed("A source order is no longer completed")
        if source.version != link.source_version:
            raise _source_changed("A source order was changed")
        if (source.billing_customer_id, source.currency, source.transaction_date) != (
            link.customer_id,
            link.currency,
            link.transaction_date,
        ):
            raise _source_changed("A source order's customer, currency or date was changed")
    source_lines = list(
        db.scalars(
            _fresh(
                scoped_select(TransactionLine, ctx)
                .where(TransactionLine.transaction_id.in_(list(by_id)))
                .order_by(TransactionLine.id)
            )
        )
    )
    # Invoicing by delivery: each invoice line is (part of) a line of its source order, priced as that line, and
    # together with every OTHER invoice it charges no more than was delivered (an order waiting until everything is
    # delivered: nothing before then). A draft made before invoicing by delivery that charges undelivered units is
    # refused here and has to be made again.
    sources_of = {line.id: line for line in source_lines}
    if len({line.source_line_id for line in lines}) != len(lines):
        raise _source_changed("A line of a source order is on this invoice twice")
    columns = (
        "description", "unit", "unit_price_ex_vat", "list_unit_price", "catalog_discount_percent", "customer_discount_percent",
        "line_discount_percent", "vat_rate",
    )
    for copy in lines:
        source_line = sources_of.get(copy.source_line_id)
        if source_line is None or copy.source_transaction_id != source_line.transaction_id or any(
            getattr(copy, column) != getattr(source_line, column) for column in columns
        ):
            raise _source_changed("A line of a source order was changed")
        if copy.ordered_quantity is not None and copy.ordered_quantity != source_line.quantity:
            raise _source_changed("A line of a source order was changed")
        if copy.quantity == source_line.quantity:
            expected = (source_line.net_amount, source_line.vat_amount, source_line.gross_amount)
        else:
            amounts = calculate_line(copy.quantity, source_line.unit_price_ex_vat, source_line.vat_rate)
            expected = (amounts.net, amounts.vat, amounts.gross)
        if (copy.net_amount, copy.vat_amount, copy.gross_amount) != expected:
            raise _source_changed("A line's amounts do not match its quantity and price")
    handed_over = delivery.delivered(db, ctx, [sources_of[line.source_line_id] for line in lines])
    # Every line of the source orders, also those not on this invoice (needed to know whether this one completes them).
    elsewhere = delivery.invoiced(db, ctx, [line.id for line in source_lines], except_invoice=invoice.id)
    if any(elsewhere.get(line.source_line_id, Decimal("0")) + line.quantity > handed_over[line.source_line_id] for line in lines):
        raise conflict(
            "more_than_delivered",
            "This draft invoice charges more than has been delivered. Delete it and invoice the order again: only what is delivered is invoiced.",
        )
    # Whether this invoice completes each order must still be true: a line added to or changed on an order since the
    # draft was built would make the stored "completes the order" wrong.
    now = {copy.source_line_id: copy.quantity for copy in lines}
    for link in links:
        order_lines = [line for line in source_lines if line.transaction_id == link.transaction_id]
        complete = all(elsewhere.get(line.id, Decimal("0")) + now.get(line.id, Decimal("0")) >= line.quantity for line in order_lines)
        if complete != link.completes_order:
            raise _source_changed("The lines of a source order were changed")
    waiting = delivery.held(db, ctx, list(by_id))
    if any(
        sources_of[line.source_line_id].transaction_id in waiting
        and delivery.shortage(db, ctx, [s for s in source_lines if s.transaction_id == sources_of[line.source_line_id].transaction_id])
        for line in lines
    ):
        raise conflict("waiting_for_delivery", "An order on this invoice waits until everything is delivered")
    # The stored header and VAT breakdown must still be the sums of the stored lines.
    net, vat, gross, by_rate = sum_lines(lines)
    if (net, vat, gross) != (invoice.net_amount, invoice.vat_amount, invoice.gross_amount):
        raise _source_changed("The stored totals do not match the stored lines")
    stored_rows = {
        row.vat_rate: (row.net_amount, row.vat_amount)
        for row in db.scalars(_fresh(scoped_select(InvoiceVatRow, ctx).where(InvoiceVatRow.invoice_id == invoice.id)))
    }
    if stored_rows != {rate: (values[0], values[1]) for rate, values in by_rate.items()}:
        raise _source_changed("The stored VAT breakdown does not match the stored lines")
    return source_lines


def issue(db: Session, ctx: TenantContext, invoice_id: uuid.UUID, if_match: str | None) -> None:
    """Freeze a draft: re-verify, re-take the historical snapshots, number it. Commits."""
    # 1. The invoice first (the same order as delete, so the two serialize on this row).
    invoice = db.scalar(_fresh(scoped_select(Invoice, ctx).where(Invoice.id == invoice_id).with_for_update()))
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    require_draft(invoice, "issued again")
    ensure_current(if_match, invoice.version, "invoice", invoice.id)

    # 2-3. Then the sources, in id order.
    links = list(
        db.scalars(
            _fresh(scoped_select(InvoiceTransaction, ctx).where(InvoiceTransaction.invoice_id == invoice.id).order_by(InvoiceTransaction.position))
        )
    )
    sources = _lock_sources(db, ctx, [link.transaction_id for link in links])
    lines = list(
        db.scalars(_fresh(scoped_select(InvoiceLine, ctx).where(InvoiceLine.invoice_id == invoice.id).order_by(InvoiceLine.position)))
    )

    # 4. Same reservation, same sources, same lines, same amounts, or nothing is issued.
    _verify_unchanged(db, ctx, invoice, links, sources, lines)

    with db.begin_nested():  # a failure anywhere below leaves no trace, the counter included
        # 5. Historical content as of issuance.
        customer = get_scoped(db, ctx, Customer, invoice.customer_id)
        organization = db.scalar(select(Organization).where(Organization.id == ctx.organization_id))
        assert customer is not None and organization is not None
        invoice.customer_snapshot = snapshots.customer_snapshot(customer)
        invoice.customer_name = customer.name
        invoice.issuer_snapshot = snapshots.issuer_snapshot(organization, ctx.user.name)
        transaction_fields = custom_fields.read_values(db, ctx, TRANSACTION, [l.transaction_id for l in links], flag=INVOICE_FLAG)
        line_fields = custom_fields.read_values(db, ctx, TRANSACTION_LINE, [l.source_line_id for l in lines], flag=INVOICE_FLAG)
        for link in links:
            link.fields = snapshots.custom_field_snapshot(transaction_fields.get(link.transaction_id, []))
        for line in lines:
            line.fields = snapshots.custom_field_snapshot(line_fields.get(line.source_line_id, []))
        db.flush()  # the children change while the invoice is still a draft

        # 6-9. The number is allocated last, after everything that can fail, in this transaction.
        number = numbering.allocate_number(db, ctx.organization_id, invoice.series)
        before = audit.snapshot(invoice, AUDITED_FIELDS)
        invoice.number = number
        invoice.number_text = numbering.format_number(number)
        invoice.issued_at = datetime.now(timezone.utc)
        invoice.issued_by = ctx.user.id
        invoice.status = InvoiceStatus.ISSUED
        invoice.version += 1
        audit.updated(db, ctx, invoice, "invoice", before, action="issued", fields=AUDITED_FIELDS)
        db.flush()
    db.commit()  # 10
