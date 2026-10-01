from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import TenantOwned


class ItemType(StrEnum):
    SERVICE = "service"
    PRODUCT = "product"


class Item(TenantOwned, Base):
    """Something an organization sells: a service or a product (industry-neutral).

    An Item holds the *current* catalog state. Transactions will copy name, price
    and VAT at the time of sale, so editing an Item never rewrites history.
    """

    __tablename__ = "items"
    __table_args__ = (
        CheckConstraint("type IN ('" + "', '".join(ItemType) + "')", name="ck_items_type"),
        CheckConstraint("price_ex_vat >= 0", name="ck_items_price_ex_vat_nonnegative"),
        CheckConstraint("vat_rate >= 0 AND vat_rate <= 100", name="ck_items_vat_rate_range"),
    )

    type: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    # Free text on purpose ("hour", "session", "pcs"); not a units subsystem.
    unit: Mapped[str] = mapped_column(String(32))
    # Money is NUMERIC/Decimal, never float. Net price: excludes VAT. Gross amounts
    # are a pricing/transaction concern and are not stored or computed here.
    price_ex_vat: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    # Percentage, e.g. 25.00 for 25 %.
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
