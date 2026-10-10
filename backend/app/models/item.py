import uuid
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKeyConstraint, Index, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base
from app.models.mixins import Authored, TenantOwned


class ItemType(StrEnum):
    SERVICE = "service"
    PRODUCT = "product"
    CHARGE = "charge"  # travel, mileage, fees...: billed like an item, never stock, kept apart from products


class Item(TenantOwned, Authored, Base):
    """Something an organization sells: a service, a product or a charge (industry-neutral).

    An Item holds the *current* catalog state. Transactions will copy name, price
    and VAT at the time of sale, so editing an Item never rewrites history.
    """

    __tablename__ = "items"
    __table_args__ = (
        # Target for tenant-safe composite foreign keys (transaction lines reference items).
        UniqueConstraint("organization_id", "id", name="uq_items_organization_id_id"),
        CheckConstraint("type IN ('" + "', '".join(ItemType) + "')", name="ck_items_type"),
        CheckConstraint("price_ex_vat >= 0", name="ck_items_price_ex_vat_nonnegative"),
        CheckConstraint("vat_rate >= 0 AND vat_rate <= 100", name="ck_items_vat_rate_range"),
        # Only a product can hold stock; a service never does.
        CheckConstraint("NOT track_stock OR type = 'product'", name="ck_items_track_stock_product"),
        CheckConstraint("sku IS NULL OR length(btrim(sku)) > 0", name="ck_items_sku_not_blank"),
        CheckConstraint("low_stock_threshold IS NULL OR low_stock_threshold >= 0", name="ck_items_low_stock_threshold"),
        # An article number identifies one item within its organization (another organization may use the same).
        Index("uq_items_organization_sku", "organization_id", "sku", unique=True, postgresql_where=text("sku IS NOT NULL")),
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
    # Article number (free text, optional, unique per organization).
    sku: Mapped[str | None] = mapped_column(String(64))
    # Whether the Inventory module keeps a stock ledger for this product (see app/modules/inventory).
    track_stock: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # Below this quantity on hand the product is "low stock" (only meaningful while it tracks stock).
    low_stock_threshold: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))


class ItemDiscount(TenantOwned, Authored, Base):
    """A temporary discount on an item: `percent` off from `starts_on` to `ends_on` (inclusive; open-ended when
    NULL), in the organization's calendar. It applies by itself when its period starts and stops when it ends;
    nothing is cleaned up. The item's own price never changes. Periods of one item never overlap (checked by the API
    under the item's row lock). A sale takes the discount active on the transaction's date, as a snapshot on the line.
    """

    __tablename__ = "item_discounts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "item_id"], ["items.organization_id", "items.id"], ondelete="CASCADE", name="fk_item_discounts_item"
        ),
        CheckConstraint("percent > 0 AND percent < 100", name="ck_item_discounts_percent_range"),
        CheckConstraint("ends_on IS NULL OR ends_on >= starts_on", name="ck_item_discounts_period"),
        Index("ix_item_discounts_item_period", "organization_id", "item_id", "starts_on"),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    percent: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(255))
