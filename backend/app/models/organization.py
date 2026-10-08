import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, SmallInteger, String, func, text
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
        # Shapes of the international standards only (ISO 13616 IBAN, ISO 9362 BIC); the check digits are not verified.
        CheckConstraint("iban IS NULL OR iban ~ '^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$'", name="ck_organizations_iban_shape"),
        CheckConstraint("bic IS NULL OR bic ~ '^[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?$'", name="ck_organizations_bic_shape"),
        CheckConstraint("payment_terms_days IS NULL OR (payment_terms_days >= 0 AND payment_terms_days <= 365)", name="ck_organizations_payment_terms_range"),
        CheckConstraint("document_language IS NULL OR document_language IN ('sv', 'en')", name="ck_organizations_document_language"),
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
    # The seller's contact and payment details as documents show them. All optional free text, except the shapes of
    # IBAN and BIC (international standards). Bankgiro and plusgiro are Swedish payment numbers, kept as typed.
    phone: Mapped[str | None] = mapped_column(String(64))
    email: Mapped[str | None] = mapped_column(String(255))
    website: Mapped[str | None] = mapped_column(String(255))
    bankgiro: Mapped[str | None] = mapped_column(String(32))
    plusgiro: Mapped[str | None] = mapped_column(String(32))
    iban: Mapped[str | None] = mapped_column(String(34))
    bic: Mapped[str | None] = mapped_column(String(11))
    # Days from the invoice date to the due date, used when an invoice is created without a due date.
    payment_terms_days: Mapped[int | None] = mapped_column(SmallInteger)
    # Whether the seller states that it is approved for F-tax (F-skatt). NULL: not stated.
    approved_for_f_tax: Mapped[bool | None] = mapped_column(Boolean)
    # The language of the organization's documents (invoice PDFs): 'sv' or 'en'. NULL: English, as before.
    document_language: Mapped[str | None] = mapped_column(String(2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
