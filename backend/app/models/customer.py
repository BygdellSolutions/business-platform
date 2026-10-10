from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, Numeric, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base
from app.models.mixins import Authored, BusinessProfile, Numbered, TenantOwned, profile_constraints


class CustomerType(StrEnum):
    PERSON = "person"
    COMPANY = "company"  # not "organization": that word means the tenant


class Customer(TenantOwned, Numbered, Authored, BusinessProfile, Base):
    """A person or company that an organization does business with."""

    __tablename__ = "customers"
    __table_args__ = (
        # Target for tenant-safe composite foreign keys: other tables reference
        # (organization_id, id), so the database itself refuses cross-tenant links.
        UniqueConstraint("organization_id", "id", name="uq_customers_organization_id_id"),
        UniqueConstraint("organization_id", "number", name="uq_customers_organization_number"),
        CheckConstraint(
            "customer_type IN ('" + "', '".join(CustomerType) + "')",
            name="ck_customers_customer_type",
        ),
        *profile_constraints("customers"),
        CheckConstraint(
            "default_discount_percent IS NULL OR (default_discount_percent > 0 AND default_discount_percent < 100)",
            name="ck_customers_default_discount_range",
        ),
    )

    customer_type: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(255))
    # Not unique: two customers may share an email, and tenants never see each other's.
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    # The customer's permanent discount (percent), applied to catalog-priced lines after any temporary catalog
    # discount, until it is changed or removed (NULL: none). Set by owners and admins; its history is recorded.
    default_discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
