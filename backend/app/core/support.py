"""Support tickets: a message from the support page is stored first, then mailed.

The order matters: the ticket is COMMITTED before any email is tried, so a provider that is down, a token that is wrong or a
deployment with no provider yet loses nothing; the ticket says what happened to its email and an operator can read it
(`admin support-tickets`) and resend it (`admin support-resend`).

Two emails are sent per ticket: the message to the support inbox (Reply-To is the sender, the ticket number is in the subject so
replies stay together), and a short confirmation to the sender with the ticket number. The confirmation holds fixed text only and
never repeats what the sender wrote, so the form cannot be used to send someone else's words to a third person.
"""

import secrets
import re
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import email as mail
from app.core.config import settings
from app.models import SupportTicket, User
from app.models.support_ticket import TICKET_OPEN

# No 0/O, 1/I/L: a number read out over the phone or typed from a screenshot is not mistaken.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
PREFIX = "SUP-"
REFERENCE = re.compile(r"^SUP-[2-9A-HJKMNP-Z]{8}$")


def new_reference() -> str:
    return PREFIX + "".join(secrets.choice(ALPHABET) for _ in range(8))


def is_reference(value: str) -> bool:
    return REFERENCE.fullmatch(value) is not None


def count_since(db: Session, since: datetime, *, email_lower: str | None = None, source: str | None = None) -> int:
    query = select(func.count()).select_from(SupportTicket).where(SupportTicket.created_at >= since)
    if email_lower is not None:
        query = query.where(SupportTicket.email_lower == email_lower)
    if source is not None:
        query = query.where(SupportTicket.source == source)
    return db.scalar(query) or 0


def create_ticket(db: Session, *, name: str | None, email: str, message: str, user: User | None, source: str | None) -> SupportTicket:
    """Store the ticket (committed) under a fresh reference. A clash of two random references is retried, not shown."""
    for _ in range(5):
        ticket = SupportTicket(
            reference=new_reference(),
            name=name or None,
            email=email,
            email_lower=email.lower(),
            message=message,
            user_id=user.id if user else None,
            source=source,
        )
        db.add(ticket)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        return ticket
    raise RuntimeError("could not find a free ticket reference")


def _to_support(ticket: SupportTicket) -> mail.EmailMessage:
    who = ticket.name or ticket.email
    lines = [
        f"Ticket {ticket.reference}",
        f"From: {who} <{ticket.email}>",
        f"Signed in: {'yes, user ' + str(ticket.user_id) if ticket.user_id else 'no'}",
        f"Received: {ticket.created_at.isoformat()}",
        "",
        ticket.message,
        "",
        "Reply to this email to answer the sender. Keep the ticket number in the subject.",
    ]
    return mail.EmailMessage(
        to=[settings.support_inbox],
        subject=f"[{ticket.reference}] Support request from {who}",
        text="\n".join(lines),
        reply_to=ticket.email,
    )


def _confirmation(ticket: SupportTicket) -> mail.EmailMessage:
    return mail.EmailMessage(
        to=[ticket.email],
        subject=f"We got your message [{ticket.reference}]",
        text=(
            f"Thank you for writing to support. Your ticket number is {ticket.reference}.\n\n"
            "We will answer by email. If you write to us again about this, keep the ticket number in the subject.\n\n"
            "If you did not send this message, you can ignore this email."
        ),
        reply_to=settings.support_inbox,
    )


def deliver(db: Session, ticket: SupportTicket) -> None:
    """Mail the ticket to support and the confirmation to the sender, then record what happened. Never raises."""
    to_support = mail.send(_to_support(ticket))
    status = "sent" if to_support.code == "sent" else "queued" if to_support.code == "queued" else "not_configured" if to_support.code == "not_configured" else "failed"
    # The confirmation is only worth sending when support itself was reached; otherwise the sender would be told "we got it" falsely.
    confirmation = mail.send(_confirmation(ticket)) if to_support.ok else None
    db.execute(
        update(SupportTicket)
        .where(SupportTicket.id == ticket.id)
        .values(
            email_status=status,
            email_error=None if to_support.ok else to_support.code,
            confirmation_status=None if confirmation is None else ("sent" if confirmation.code == "sent" else "queued" if confirmation.code == "queued" else "failed"),
        )
    )
    db.commit()


def open_tickets(db: Session, *, limit: int = 100) -> list[SupportTicket]:
    return list(db.scalars(select(SupportTicket).where(SupportTicket.status == TICKET_OPEN).order_by(SupportTicket.created_at.desc()).limit(limit)))


def ticket_by_reference(db: Session, reference: str) -> SupportTicket | None:
    return db.scalar(select(SupportTicket).where(SupportTicket.reference == reference.strip().upper()))


def window_start(now: datetime) -> datetime:
    return now - timedelta(hours=1)


def erase_user_tickets(db: Session, user_id: uuid.UUID) -> int:
    """An account is erased: the tickets it sent lose the person's name, address and words (the ticket numbers and dates stay)."""
    result = db.execute(
        update(SupportTicket)
        .where(SupportTicket.user_id == user_id)
        .values(user_id=None, name=None, email="erased", email_lower="erased", message="[erased]", source=None)
    )
    return result.rowcount or 0
