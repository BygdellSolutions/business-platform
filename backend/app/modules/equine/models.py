import uuid
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import TenantOwned

# Sanity bounds enforced by the database; the API additionally rejects future years.
MIN_BIRTH_YEAR = 1900
MAX_BIRTH_YEAR = 2100


class HorseSex(StrEnum):
    MARE = "mare"
    STALLION = "stallion"
    GELDING = "gelding"


class Horse(TenantOwned, Base):
    """A horse. Not a customer.

    Owner and stable are two separate references to Customers of the SAME
    organization. Neither implies the other, and neither is the billing customer:
    billing belongs to the future transaction, never to the horse.

    The foreign keys are composite (organization_id, customer id), so the database
    itself refuses a reference to another organization's customer.
    """

    __tablename__ = "horses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "owner_customer_id"],
            ["customers.organization_id", "customers.id"],
            ondelete="RESTRICT",
            name="fk_horses_owner_same_organization",
        ),
        # MATCH SIMPLE (the default): a NULL stable_customer_id skips the check.
        ForeignKeyConstraint(
            ["organization_id", "stable_customer_id"],
            ["customers.organization_id", "customers.id"],
            ondelete="RESTRICT",
            name="fk_horses_stable_same_organization",
        ),
        CheckConstraint("sex IN ('" + "', '".join(HorseSex) + "')", name="ck_horses_sex"),
        CheckConstraint(
            f"birth_year BETWEEN {MIN_BIRTH_YEAR} AND {MAX_BIRTH_YEAR}",
            name="ck_horses_birth_year_range",
        ),
        # Serve the "horses of this owner/stable" filter.
        Index("ix_horses_organization_owner", "organization_id", "owner_customer_id"),
        Index("ix_horses_organization_stable", "organization_id", "stable_customer_id"),
    )

    name: Mapped[str] = mapped_column(String(255))
    owner_customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    stable_customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    birth_year: Mapped[int | None] = mapped_column(SmallInteger)
    sex: Mapped[str | None] = mapped_column(String(16))
    breed: Mapped[str | None] = mapped_column(String(100))  # free text on purpose
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
