"""Emailing an issued invoice to a customer.

The PDF that is attached is the invoice's own frozen PDF (the very bytes a download gives, never a fresh rendering), and the text
is built from the stored invoice and its issuer snapshot only, so what the customer receives is what the invoice says whatever the
live customer or organization records say today.

  * Only an ISSUED invoice, only by the people who do the books (owner, admin, accountant); another organization's invoice is a 404.
  * From "<organization>@<invoice_email_domain>" with the organization's name as the sender name, so the receiver sees who it is
    from; the domain is a sending subdomain onboarded for the platform. Replies go to the organization's own email (the one frozen
    on the invoice), else to the person who pressed send.
  * Every attempt is logged (to whom, when, by whom, the hash of the attachment, what the provider said) and recorded in the
    invoice's history; a failed attempt is kept and counts against the organization's daily limit.
  * The email is sent inside the request with a short timeout; a provider that is down or not set up is reported to the person, who
    can press send again. Nothing is queued behind their back.
"""

import hashlib
import re
import unicodedata
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typing import Annotated

from app.core import audit, clock, message_templates, rate_limit
from app.core import email as mail
from app.core.authz import roles_required
from app.core.config import settings
from app.core.db import get_db
from app.core.tenant import TenantContext, get_tenant_context
from app.core.tenant_scope import create_scoped, get_scoped_or_404, scoped_select
from app.models import Organization, Role, User
from app.modules.invoicing import ocr
from app.modules.invoicing.models import Invoice, InvoiceEmail, InvoiceStatus
from app.modules.invoicing.pdf import service as pdf_service

router = APIRouter(tags=["invoices"])  # included into the invoices router, which carries the /api/invoices prefix

# Reading the log is open to every member who can read the invoice; sending is for the people who do the books.
senders = roles_required(Role.OWNER, Role.ADMIN, Role.ACCOUNTANT)
MAX_ATTACHMENT_BYTES = mail.MAX_MESSAGE_BYTES - 512 * 1024  # base64 grows the file by a third: leave room for it and the text


class InvoiceEmailSend(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: Annotated[str, StringConstraints(strip_whitespace=True, max_length=254)]
    # The subject and the message as the person saw them in the form (pre-filled from the organization's templates); none: the built-in subject.
    subject: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None


class InvoiceEmailDraft(BaseModel):
    """What the send form starts with: the customer's address and the subject and message from the organization's templates, filled in."""

    to: str
    subject: str
    message: str


class InvoiceEmailRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    to_email: str
    subject: str
    note: str | None
    status: str
    error: str | None
    created_at: datetime
    sent_by_name: str | None = None


def sender_local_part(organization_name: str) -> str:
    """The part before the @: the organization's name as a plain lower-case address ("Umeå Häst & Rehab" -> "umea-hast-rehab")."""
    ascii_name = unicodedata.normalize("NFKD", organization_name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:40].strip("-")
    return slug or "invoices"


def _money(amount: Decimal, currency: str, language: str) -> str:
    whole, _, cents = format(amount, "f").partition(".")
    sign, digits = ("-", whole[1:]) if whole.startswith("-") else ("", whole)
    groups: list[str] = []
    while digits:
        groups.insert(0, digits[-3:])
        digits = digits[:-3]
    grouped = " ".join(groups) or "0"
    separator = "," if language == "sv" else "."
    return f"{sign}{grouped}{separator}{(cents or '00').ljust(2, '0')} {currency}"


def _day(value: date | None) -> str:
    return value.isoformat() if value else "-"


def _language(invoice: Invoice) -> str:
    return "sv" if invoice.issuer_snapshot.get("document_language") != "en" else "en"


def default_subject(invoice: Invoice) -> str:
    organization = str(invoice.issuer_snapshot.get("name") or "")
    return f"Faktura {invoice.number_text} från {organization}" if _language(invoice) == "sv" else f"Invoice {invoice.number_text} from {organization}"


def placeholder_values(invoice: Invoice) -> dict[str, str]:
    """The facts a template can use, all from the stored invoice (never from today's customer or organization records)."""
    return {
        "customer": invoice.customer_name,
        "invoice_number": str(invoice.number_text or ""),
        "amount": _money(invoice.gross_amount, invoice.currency, _language(invoice)),
        "due_date": _day(invoice.due_date) if invoice.due_date else "",
        "payment_reference": ocr.ocr_reference(invoice.number) if invoice.number is not None else "",
        "organization": str(invoice.issuer_snapshot.get("name") or ""),
    }


def compose(invoice: Invoice, *, note: str | None, language: str, subject: str | None = None) -> tuple[str, str]:
    """The subject and the text, from the stored invoice and its issuer snapshot only."""
    issuer = invoice.issuer_snapshot
    organization = str(issuer.get("name") or "")
    sv = language == "sv"
    amount = _money(invoice.gross_amount, invoice.currency, language)
    subject = subject or default_subject(invoice)
    lines = [
        "Hej," if sv else "Hello,",
        "",
        f"Här kommer faktura {invoice.number_text} från {organization}." if sv else f"Here is invoice {invoice.number_text} from {organization}.",
        "",
        f"Belopp att betala: {amount}" if sv else f"Amount to pay: {amount}",
        f"Förfallodatum: {_day(invoice.due_date)}" if sv else f"Due date: {_day(invoice.due_date)}",
    ]
    if invoice.number is not None:
        lines.append(f"Betalningsreferens (OCR): {ocr.ocr_reference(invoice.number)}" if sv else f"Payment reference (OCR): {ocr.ocr_reference(invoice.number)}")
    for key, label in (("bankgiro", "Bankgiro"), ("plusgiro", "Plusgiro"), ("iban", "IBAN"), ("bic", "BIC")):
        if issuer.get(key):
            lines.append(f"{label}: {issuer[key]}")
    if note:
        lines += ["", note]
    lines += ["", "Fakturan finns som PDF i bilagan." if sv else "The invoice is attached as a PDF."]
    contact = " · ".join(str(issuer[key]) for key in ("email", "phone") if issuer.get(key))
    lines += [
        "",
        (f"Frågor om fakturan? Kontakta {organization}" if sv else f"Questions about the invoice? Contact {organization}") + (f": {contact}" if contact else "."),
        "",
        "Med vänlig hälsning" if sv else "Kind regards",
        organization,
    ]
    return subject, "\n".join(lines)


def _sent_by(db: Session, rows: list[InvoiceEmail]) -> list[InvoiceEmailRead]:
    people = {row.created_by for row in rows}
    people.discard(None)
    names = dict(db.execute(select(User.id, User.name).where(User.id.in_(people))).all()) if people else {}
    return [InvoiceEmailRead.model_validate(row).model_copy(update={"sent_by_name": names.get(row.created_by)}) for row in rows]


def _issued_invoice(db: Session, ctx: TenantContext, invoice_id: uuid.UUID) -> Invoice:
    invoice = get_scoped_or_404(db, ctx, Invoice, invoice_id)  # another organization's invoice is a 404
    if invoice.status != InvoiceStatus.ISSUED:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "invoice_not_issued", "message": "Only an issued invoice can be emailed. Issue the invoice first."})
    return invoice


@router.get("/{invoice_id}/emails", response_model=list[InvoiceEmailRead])
def list_invoice_emails(invoice_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)) -> list[InvoiceEmailRead]:
    """Every attempt to email this invoice, newest first."""
    get_scoped_or_404(db, ctx, Invoice, invoice_id)
    rows = list(db.scalars(scoped_select(InvoiceEmail, ctx).where(InvoiceEmail.invoice_id == invoice_id).order_by(InvoiceEmail.created_at.desc(), InvoiceEmail.id).limit(100)))
    return _sent_by(db, rows)


@router.get("/{invoice_id}/email-draft", response_model=InvoiceEmailDraft)
def invoice_email_draft(invoice_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)) -> InvoiceEmailDraft:
    """The send form's starting point: the customer's address, and the subject and message from the organization's templates with this
    invoice's facts filled in (or the built-in subject and an empty message when it has none)."""
    invoice = _issued_invoice(db, ctx, invoice_id)
    organization = db.execute(select(Organization).where(Organization.id == ctx.organization_id)).scalar_one()
    values = placeholder_values(invoice)
    customer_email = invoice.customer_snapshot.get("email")
    return InvoiceEmailDraft(
        to=customer_email if isinstance(customer_email, str) else "",
        subject=message_templates.render(organization.invoice_email_subject, values) if organization.invoice_email_subject else default_subject(invoice),
        message=message_templates.render(organization.invoice_email_message, values) if organization.invoice_email_message else "",
    )


@router.post("/{invoice_id}/email", response_model=InvoiceEmailRead, status_code=status.HTTP_201_CREATED)
def email_invoice(
    invoice_id: uuid.UUID,
    payload: InvoiceEmailSend,
    ctx: TenantContext = Depends(senders),
    _heavy: None = Depends(rate_limit.heavy),
    db: Session = Depends(get_db),
) -> InvoiceEmailRead:
    if not mail.looks_like_address(payload.to):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[{"loc": ["body", "to"], "msg": "Enter an email address the invoice can be sent to, like name@example.com.", "type": "value_error"}],
        )
    invoice = _issued_invoice(db, ctx, invoice_id)

    since = clock.utcnow() - timedelta(days=1)
    sent_today = db.scalar(select(func.count()).select_from(InvoiceEmail).where(InvoiceEmail.organization_id == ctx.organization_id, InvoiceEmail.created_at >= since)) or 0
    if sent_today >= settings.invoice_emails_per_day:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "rate_limited", "message": "This organization has sent as many invoice emails as are allowed in a day. Try again tomorrow."},
            headers={"Retry-After": "3600"},
        )

    stored = pdf_service.get_or_create_pdf(db, ctx, invoice.id)  # the frozen bytes, the same as a download
    if len(stored.content) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "invoice_pdf_too_large", "message": "The invoice PDF is too large to send by email. Download it and send it another way."},
        )
    issuer = invoice.issuer_snapshot
    organization = str(issuer.get("name") or "")
    language = "sv" if issuer.get("document_language") != "en" else "en"
    subject, text = compose(invoice, note=payload.note or None, language=language, subject=payload.subject or None)
    sender = f"{sender_local_part(organization)}@{settings.invoice_email_domain}"
    reply_to = issuer.get("email") if isinstance(issuer.get("email"), str) and mail.looks_like_address(issuer["email"]) else ctx.user.email

    outcome = mail.send(
        mail.EmailMessage(
            to=[payload.to],
            subject=subject,
            text=text,
            reply_to=reply_to,
            from_address=sender,
            from_name=organization or None,
            attachments=[mail.Attachment(filename=stored.filename, content=stored.content, type="application/pdf")],
        )
    )
    row = create_scoped(
        db,
        ctx,
        InvoiceEmail,
        invoice_id=invoice.id,
        to_email=payload.to,
        reply_to=reply_to,
        sender_address=sender,
        subject=subject[:200],
        note=payload.note or None,
        pdf_sha256=hashlib.sha256(stored.content).hexdigest(),
        status=(outcome.code if outcome.ok else "failed"),
        error=None if outcome.ok else outcome.code,
    )
    audit.stamp(row, ctx, created=True)
    audit.record(
        db,
        ctx,
        entity_type="invoice",
        entity_id=invoice.id,
        action="emailed" if outcome.ok else "email_failed",
        changes={"to": {"from": None, "to": payload.to}, **({} if outcome.ok else {"error": {"from": None, "to": outcome.code}})},
    )
    db.commit()
    db.refresh(row)
    return _sent_by(db, [row])[0]
