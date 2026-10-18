"""Emailing the receipt of an order paid at the counter to the customer.

The same rules as emailing an invoice (see emailing.py): the PDF attached is the receipt as the download makes it (from the organization as
it is now, with its current logo), the email comes from "<organization>@<invoice_email_domain>" with the organization's name as the sender,
replies go to the organization (else to the person who sent it), every attempt is logged (receipt_emails) and recorded in the order's history,
and the organization has a daily limit that failed attempts count against.

Who may send: anyone who may change records (owner, admin, accountant, employee), because the person at the till is often an employee and a
customer asks for the receipt there and then. A walk-in customer has no address, so the person types one.
"""

import hashlib
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit, clock, rate_limit
from app.core import email as mail
from app.core.authz import record_writer
from app.core.config import settings
from app.core.db import get_db
from app.core.org_time import today_in
from app.core.tenant import TenantContext, get_tenant_context
from app.core.tenant_scope import create_scoped, get_scoped, get_scoped_or_404, scoped_select
from app.models import Customer, Organization
from app.modules.invoicing import receipts, sent
from app.modules.invoicing.emailing import (
    MAX_ATTACHMENT_BYTES,
    InvoiceEmailDraft,
    InvoiceEmailRead,
    InvoiceEmailSend,
    _day,
    _money,
    _sent_by,
    sender_local_part,
)
from app.modules.invoicing.models import ReceiptEmail
from app.modules.invoicing.pdf.labels import labels
from app.modules.sales.models import Transaction, TransactionLine
from app.modules.sales.pricing import calculate_totals

router = APIRouter(tags=["invoices"])  # included into the invoices router: /api/invoices/receipts/{transaction_id}/...


class _Facts:
    def __init__(self, order: Transaction, organization: Organization, customer: Customer | None, gross, language: str):
        self.order, self.organization, self.customer, self.gross, self.language = order, organization, customer, gross, language
        self.name = organization.name
        self.number = order.receipt_number_text or ""


def _facts(db: Session, ctx: TenantContext, transaction_id: uuid.UUID) -> _Facts:
    order = get_scoped_or_404(db, ctx, Transaction, transaction_id)  # another organization's order is a 404
    if order.paid_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "not_paid_at_counter", "message": "Only an order paid at the counter has a receipt."})
    lines = list(db.scalars(scoped_select(TransactionLine, ctx).where(TransactionLine.transaction_id == order.id)))
    organization = db.execute(select(Organization).where(Organization.id == ctx.organization_id)).scalar_one()
    customer = get_scoped(db, ctx, Customer, order.billing_customer_id)
    language = "sv" if organization.document_language != "en" else "en"
    return _Facts(order, organization, customer, calculate_totals(lines).gross_amount, language)


def default_subject(facts: _Facts) -> str:
    return f"Kvitto {facts.number}" if facts.language == "sv" else f"Receipt {facts.number}"


def compose(facts: _Facts, *, note: str | None, subject: str | None) -> tuple[str, str]:
    """The subject and the text, from the order and the organization's own details."""
    sv = facts.language == "sv"
    organization = facts.name
    words = labels(facts.language)
    paid_on = today_in(facts.organization.timezone, facts.order.paid_at)
    amount = _money(facts.gross, facts.order.currency, facts.language)
    method = words.get(f"method_{facts.order.payment_method}", str(facts.order.payment_method))
    lines = [
        "Hej," if sv else "Hello,",
        "",
        f"Tack för ditt köp hos {organization}. Här är ditt kvitto {facts.number}." if sv else f"Thank you for your purchase at {organization}. Here is your receipt {facts.number}.",
        "",
        f"Betalt: {amount}" if sv else f"Paid: {amount}",
        f"Betalsätt: {method}" if sv else f"Paid by: {method}",
        f"Datum: {_day(paid_on)}" if sv else f"Date: {_day(paid_on)}",
    ]
    if note:
        lines += ["", note]
    lines += ["", "Kvittot finns som PDF i bilagan." if sv else "The receipt is attached as a PDF."]
    contact = " · ".join(str(value) for value in (facts.organization.email, facts.organization.phone) if value)
    lines += [
        "",
        (f"Frågor? Kontakta {organization}" if sv else f"Questions? Contact {organization}") + (f": {contact}" if contact else "."),
        "",
        "Med vänlig hälsning" if sv else "Kind regards",
        organization,
    ]
    return subject or default_subject(facts), "\n".join(lines)


@router.get("/receipts/{transaction_id}/emails", response_model=list[InvoiceEmailRead])
def list_receipt_emails(transaction_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)) -> list[InvoiceEmailRead]:
    """Every attempt to email this receipt, newest first."""
    get_scoped_or_404(db, ctx, Transaction, transaction_id)
    rows = list(
        db.scalars(
            scoped_select(ReceiptEmail, ctx).where(ReceiptEmail.transaction_id == transaction_id).order_by(ReceiptEmail.created_at.desc(), ReceiptEmail.id).limit(100)
        )
    )
    return _sent_by(db, rows)  # type: ignore[arg-type]  (the same columns as an invoice email's)


@router.get("/receipts/{transaction_id}/email-draft", response_model=InvoiceEmailDraft)
def receipt_email_draft(transaction_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)) -> InvoiceEmailDraft:
    """The send form's starting point: the customer's address (none for a walk-in), the built-in subject and an empty message."""
    facts = _facts(db, ctx, transaction_id)
    address = facts.customer.email if facts.customer is not None and not getattr(facts.customer, "walk_in", False) else None
    return InvoiceEmailDraft(to=address or "", subject=default_subject(facts), message="")


@router.post("/receipts/{transaction_id}/email", response_model=InvoiceEmailRead, status_code=status.HTTP_201_CREATED)
def email_receipt(
    transaction_id: uuid.UUID,
    payload: InvoiceEmailSend,
    ctx: TenantContext = Depends(record_writer),
    _heavy: None = Depends(rate_limit.heavy),
    db: Session = Depends(get_db),
) -> InvoiceEmailRead:
    if not mail.looks_like_address(payload.to):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[{"loc": ["body", "to"], "msg": "Enter an email address the receipt can be sent to, like name@example.com.", "type": "value_error"}],
        )
    facts = _facts(db, ctx, transaction_id)
    if sent.emails_since(db, ctx.organization_id, clock.utcnow() - timedelta(days=1)) >= settings.invoice_emails_per_day:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "rate_limited", "message": "This organization has sent as many emails as are allowed in a day. Try again tomorrow."},
            headers={"Retry-After": "3600"},
        )
    content, filename = receipts.receipt_pdf(db, ctx, transaction_id)  # the receipt as a download makes it
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "receipt_pdf_too_large", "message": "The receipt PDF is too large to send by email. Download it and send it another way."},
        )
    subject, text = compose(facts, note=payload.note or None, subject=payload.subject or None)
    sender = f"{sender_local_part(facts.name)}@{settings.invoice_email_domain}"
    reply_to = facts.organization.email if facts.organization.email and mail.looks_like_address(facts.organization.email) else ctx.user.email

    outcome = mail.send(
        mail.EmailMessage(
            to=[payload.to],
            subject=subject,
            text=text,
            reply_to=reply_to,
            from_address=sender,
            from_name=facts.name or None,
            attachments=[mail.Attachment(filename=filename, content=content, type="application/pdf")],
        )
    )
    row = create_scoped(
        db,
        ctx,
        ReceiptEmail,
        transaction_id=facts.order.id,
        to_email=payload.to,
        reply_to=reply_to,
        sender_address=sender,
        subject=subject[:200],
        note=payload.note or None,
        pdf_sha256=hashlib.sha256(content).hexdigest(),
        status=(outcome.code if outcome.ok else "failed"),
        error=None if outcome.ok else outcome.code,
    )
    audit.stamp(row, ctx, created=True)
    audit.record(
        db,
        ctx,
        entity_type="transaction",
        entity_id=facts.order.id,
        action="receipt_emailed" if outcome.ok else "receipt_email_failed",
        changes={"to": {"from": None, "to": payload.to}, **({} if outcome.ok else {"error": {"from": None, "to": outcome.code}})},
    )
    db.commit()
    db.refresh(row)
    return _sent_by(db, [row])[0]  # type: ignore[list-item]
