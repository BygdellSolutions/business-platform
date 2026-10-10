from sqlalchemy import Boolean, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base
from app.models.mixins import Authored, BusinessProfile, TenantOwned, profile_constraints


class Supplier(TenantOwned, Authored, BusinessProfile, Base):
    """A company or person an organization buys goods from (the buying side's counterpart of a customer).

    Chosen from this register wherever goods are ordered (incoming stock), so the same supplier is always the same
    record, never a differently spelled name.
    """

    __tablename__ = "suppliers"
    __table_args__ = (
        # Target for tenant-safe composite foreign keys (incoming stock refers to (organization_id, id)).
        UniqueConstraint("organization_id", "id", name="uq_suppliers_organization_id_id"),
        *profile_constraints("suppliers"),
    )

    name: Mapped[str] = mapped_column(String(255))
    contact_person: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(64))
    # The organization's own customer number at the supplier (to quote when ordering).
    our_customer_number: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
