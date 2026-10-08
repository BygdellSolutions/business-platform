"""Payments recorded by hand for issued invoices.

Every change happens under the invoice's row lock, so two people recording payments at the same moment can never pay
an invoice more than its gross amount together. Payment rows are never changed or deleted: a mistake is undone by a
reversal (the negative amount, naming the payment it cancels), and the history shows both.
"""

import uuid
from collections.abc import Sequence
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.core import audit, clock
from app.core.org_time import organization_today
from app.core.tenant import TenantContext
from app.core.tenant_scope import get_scoped_or_404, reference_error
from app.models import User
from app.modules.invoicing.models import Invoice, InvoicePayment, InvoiceStatus
from app.modules.invoicing.schemas import InvoicePaymentRead, PaymentCreate

ZERO = Decimal("0.00")


def paid_amounts(db: Session, organization_id: uuid.UUID, invoice_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Decimal]:
    """What has been paid per invoice: the sum of its payment rows (reversals are negative)."""
    if not invoice_ids:
        return {}
    rows = db.execute(
        select(InvoicePayment.invoice_id, func.sum(InvoicePayment.amount))
        .where(InvoicePayment.organization_id == organization_id, InvoicePayment.invoice_id.in_(list(invoice_ids)))
        .group_by(InvoicePayment.invoice_id)
    )
    return {invoice_id: amount for invoice_id, amount in rows}


def payment_fields(invoice: Invoice, paid: Decimal) -> dict:
    """Paid, outstanding and the payment state of an issued invoice; nothing for a draft (it cannot be paid)."""
    if invoice.status != InvoiceStatus.ISSUED:
        return dict(paid_amount=None, outstanding_amount=None, payment_status=None)
    state = "paid" if paid >= invoice.gross_amount else "partially_paid" if paid > 0 else "unpaid"
    return dict(paid_amount=paid, outstanding_amount=invoice.gross_amount - paid, payment_status=state)


def paid_sum_expression():
    """The paid amount of the invoice in the surrounding query, for filtering lists by payment state."""
    return (
        select(func.coalesce(func.sum(InvoicePayment.amount), 0))
        .where(InvoicePayment.organization_id == Invoice.organization_id, InvoicePayment.invoice_id == Invoice.id)
        .scalar_subquery()
    )


def list_payments(db: Session, ctx: TenantContext, invoice_id: uuid.UUID) -> list[InvoicePaymentRead]:
    reversal = aliased(InvoicePayment)
    reversed_ids = select(reversal.reverses_payment_id).where(
        reversal.organization_id == ctx.organization_id, reversal.invoice_id == invoice_id, reversal.reverses_payment_id.is_not(None)
    )
    done = set(db.scalars(reversed_ids))
    rows = db.execute(
        select(InvoicePayment, User.name)
        .outerjoin(User, User.id == InvoicePayment.created_by)
        .where(InvoicePayment.organization_id == ctx.organization_id, InvoicePayment.invoice_id == invoice_id)
        .order_by(InvoicePayment.created_at, InvoicePayment.id)
    ).all()
    return [
        InvoicePaymentRead(
            id=row.id,
            amount=row.amount,
            paid_on=row.paid_on,
            method=row.method,
            reference=row.reference,
            note=row.note,
            reverses_payment_id=row.reverses_payment_id,
            reversed=row.id in done,
            created_at=row.created_at,
            created_by_name=name,
        )
        for row, name in rows
    ]


def _locked_issued(db: Session, ctx: TenantContext, invoice_id: uuid.UUID) -> Invoice:
    invoice = get_scoped_or_404(db, ctx, Invoice, invoice_id, for_update=True)
    if invoice.status != InvoiceStatus.ISSUED:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail={"code": "invoice_not_issued", "message": "Only an issued invoice can be paid. Issue the invoice first."}
        )
    return invoice


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


def record_payment(db: Session, ctx: TenantContext, invoice_id: uuid.UUID, payload: PaymentCreate) -> None:
    invoice = _locked_issued(db, ctx, invoice_id)
    if payload.paid_on > organization_today(db, ctx.organization_id):
        reference_error("paid_on", "A payment date cannot be in the future", "payment.future_date")
    paid = paid_amounts(db, ctx.organization_id, [invoice.id]).get(invoice.id, ZERO)
    outstanding = invoice.gross_amount - paid
    if payload.amount > outstanding:
        reference_error("amount", f"Only {_money(outstanding)} {invoice.currency} is outstanding on this invoice", "payment.overpaid")
    # Stamped from the application clock: the list is in the order payments were recorded, even within one transaction.
    row = InvoicePayment(organization_id=ctx.organization_id, invoice_id=invoice.id, created_by=ctx.user.id, created_at=clock.utcnow(), **payload.model_dump())
    db.add(row)
    db.flush()
    audit.record(
        db,
        ctx,
        entity_type="invoice",
        entity_id=invoice.id,
        action="payment_recorded",
        changes={
            "payment": {"from": None, "to": f"{_money(payload.amount)} {invoice.currency}, {payload.method}, {payload.paid_on.isoformat()}"},
            "outstanding": {"from": _money(outstanding), "to": _money(outstanding - payload.amount)},
        },
    )
    db.commit()


def reverse_payment(db: Session, ctx: TenantContext, invoice_id: uuid.UUID, payment_id: uuid.UUID, note: str | None) -> None:
    invoice = _locked_issued(db, ctx, invoice_id)
    payment = db.scalar(
        select(InvoicePayment).where(
            InvoicePayment.organization_id == ctx.organization_id, InvoicePayment.invoice_id == invoice.id, InvoicePayment.id == payment_id
        )
    )
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    if payment.reverses_payment_id is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "payment_is_reversal", "message": "A reversal cannot itself be reversed."})
    already = db.scalar(
        select(InvoicePayment.id).where(InvoicePayment.organization_id == ctx.organization_id, InvoicePayment.reverses_payment_id == payment.id)
    )
    if already is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "payment_already_reversed", "message": "This payment was already reversed."})
    paid = paid_amounts(db, ctx.organization_id, [invoice.id]).get(invoice.id, ZERO)
    db.add(
        InvoicePayment(
            organization_id=ctx.organization_id,
            invoice_id=invoice.id,
            amount=-payment.amount,
            paid_on=organization_today(db, ctx.organization_id),
            method=payment.method,
            reference=payment.reference,
            note=note,
            reverses_payment_id=payment.id,
            created_by=ctx.user.id,
            created_at=clock.utcnow(),
        )
    )
    db.flush()
    audit.record(
        db,
        ctx,
        entity_type="invoice",
        entity_id=invoice.id,
        action="payment_reversed",
        changes={
            "payment": {"from": f"{_money(payment.amount)} {invoice.currency}, {payment.method}, {payment.paid_on.isoformat()}", "to": None},
            "outstanding": {"from": _money(invoice.gross_amount - paid), "to": _money(invoice.gross_amount - paid + payment.amount)},
        },
    )
    db.commit()
