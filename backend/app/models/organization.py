import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base
from app.models.mixins import BusinessProfile, profile_constraints


class Organization(BusinessProfile, Base):
    """A tenant: a company using business-platform.

    This is the one tenant-level table that has no `organization_id` column,
    because the row *is* the organization. Every tenant-owned table references
    `organizations.id`.
    """

    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint("default_currency IS NULL OR default_currency ~ '^[A-Z]{3}$'", name="ck_organizations_default_currency_shape"),
        # Shape only; whether the name is a real IANA zone is the API's check (the database has no zone list).
        CheckConstraint("timezone IS NULL OR timezone ~ '^[A-Za-z0-9_+/-]{1,64}$'", name="ck_organizations_timezone_shape"),
        *profile_constraints("organizations"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(255))
    # The currency new transactions are created in (a three-capital-letter code such as in ISO 4217;
    # the shape is checked, nothing else). NULL until an owner or admin sets it: there is no
    # assumed default. Changing it is refused once items or transactions exist (see app/api/organization.py).
    default_currency: Mapped[str | None] = mapped_column(String(3))
    # The organization's own legal name as it should appear on documents (the display `name` stays as is).
    legal_name: Mapped[str | None] = mapped_column(String(255))
    # The IANA time zone the organization works in (e.g. Europe/Stockholm). It decides what "today" is for
    # date defaults. NULL until an owner or admin sets it: then dates default to UTC, as before (no assumed zone).
    timezone: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
