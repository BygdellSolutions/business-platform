import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base


class AuditEvent(Base):
    """One change to a business record: who, when, which record, what happened and what changed.

    Tenant-owned and append-only (a trigger refuses UPDATE, and DELETE except while the organization itself is
    being deleted). `changes` maps a field to `{"from": old, "to": new}` (JSON-safe values; decimals as strings);
    a creation has `from: null`, a deletion `to: null`. `context_*` names the record this one belongs to (a line's
    transaction), so a transaction's history includes its lines. Written in the same database transaction as the
    change it describes, so there is never a change without its event or the other way round.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_entity", "organization_id", "entity_type", "entity_id", "occurred_at"),
        Index("ix_audit_events_context", "organization_id", "context_type", "context_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    context_type: Mapped[str | None] = mapped_column(String(64))
    context_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(32))
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
