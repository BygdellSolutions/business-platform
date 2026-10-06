"""The record that makes organization creation safe to retry.

A browser that creates an organization may lose the response after the server committed. It retries with the
SAME client-generated key; this row (one per creator and key) then makes the retry return the organization that
was created instead of creating another. The row is inserted in the SAME transaction as the organization and
its owner membership, so it exists exactly when they do. There is no uniqueness on organization names: two
tenants may legitimately share a display name.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base


class OrganizationCreationRequest(Base):
    __tablename__ = "organization_creation_requests"
    __table_args__ = (
        CheckConstraint("request_key ~ '^[A-Za-z0-9_-]{43}$'", name="ck_organization_creation_requests_key_shape"),
        CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="ck_organization_creation_requests_hash_shape"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    request_key: Mapped[str] = mapped_column(String(43), primary_key=True)
    # SHA-256 of the validated request body: the same key with a DIFFERENT body is refused, never replayed.
    request_hash: Mapped[str] = mapped_column(String(64))
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
