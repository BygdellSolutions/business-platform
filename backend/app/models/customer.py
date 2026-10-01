from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import TenantOwned


class CustomerType(StrEnum):
    PERSON = "person"
    COMPANY = "company"  # not "organization": that word means the tenant


class Customer(TenantOwned, Base):
    """A person or company that an organization does business with."""

    __tablename__ = "customers"
    __table_args__ = (
        CheckConstraint(
            "customer_type IN ('" + "', '".join(CustomerType) + "')",
            name="ck_customers_customer_type",
        ),
    )

    customer_type: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(255))
    # Not unique: two customers may share an email, and tenants never see each other's.
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
