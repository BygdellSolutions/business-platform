import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base

TICKET_OPEN, TICKET_CLOSED = "open", "closed"
MAX_MESSAGE = 4000


class SupportTicket(Base):
    """A message to support from the support page, kept the moment it arrives (before any email is tried).

    Platform-level on purpose: it belongs to no organization (a person who cannot sign in writes here too), so it has NO
    `organization_id` and is never part of an organization's export or deletion. `user_id` is set only when the sender was signed
    in (the server's own knowledge, never the form's), and is cleared if the account is erased. `reference` is what the sender is
    told and what the email subject carries: short, random and not sequential, so it reveals nothing about how many there are.
    """

    __tablename__ = "support_tickets"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'closed')", name="ck_support_tickets_status"),
        CheckConstraint("email_status IN ('pending', 'sent', 'queued', 'not_configured', 'failed')", name="ck_support_tickets_email_status"),
        CheckConstraint(f"length(btrim(message)) > 0 AND length(message) <= {MAX_MESSAGE}", name="ck_support_tickets_message_length"),
        Index("ix_support_tickets_email_created", "email_lower", "created_at"),
        Index("ix_support_tickets_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    reference: Mapped[str] = mapped_column(String(16), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(String(10), default=TICKET_OPEN, server_default=TICKET_OPEN)
    name: Mapped[str | None] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254))
    email_lower: Mapped[str] = mapped_column(String(254))  # for the per-address limit
    message: Mapped[str] = mapped_column(Text)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    source: Mapped[str | None] = mapped_column(String(64))  # the sender's network address as a throttling key, never shown
    # What happened to the email to support: pending (not tried), sent, queued (the provider will deliver), not_configured
    # (no provider yet: an operator reads it with `admin support-tickets`), failed (see email_error).
    email_status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    email_error: Mapped[str | None] = mapped_column(String(32))
    confirmation_status: Mapped[str | None] = mapped_column(String(16))  # the same for the confirmation sent to the sender
