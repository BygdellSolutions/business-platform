import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base
from app.models.mixins import Authored, Numbered, TenantOwned



def discount_constraints(table: str) -> tuple[CheckConstraint, ...]:
    """The discount layers of a line, as the database enforces them (the same rule as
    `pricing.discounted_unit_price`): a line either has no list price and no discounts (ad-hoc or a manually set
    price without a discount), or its unit price is the list price after the catalog layer, rounded, then the customer
    layer, rounded, then the line's own discount, rounded."""
    percent = "{0} IS NULL OR ({0} > 0 AND {0} < 100)"
    return (
        CheckConstraint(percent.format("catalog_discount_percent"), name=f"ck_{table}_catalog_discount_range"),
        CheckConstraint(percent.format("customer_discount_percent"), name=f"ck_{table}_customer_discount_range"),
        CheckConstraint(percent.format("line_discount_percent"), name=f"ck_{table}_line_discount_range"),
        CheckConstraint(
            "(list_unit_price IS NULL AND catalog_discount_percent IS NULL AND customer_discount_percent IS NULL AND line_discount_percent IS NULL)"
            " OR (list_unit_price IS NOT NULL AND unit_price_ex_vat = round(round(round(list_unit_price * (100 - coalesce(catalog_discount_percent, 0)) / 100, 2) * (100 - coalesce(customer_discount_percent, 0)) / 100, 2) * (100 - coalesce(line_discount_percent, 0)) / 100, 2))",
            name=f"ck_{table}_discount_layers",
        ),
    )

class TransactionStatus(StrEnum):
    """Lifecycle of the transaction itself (not its invoicing).

    draft      - being entered; the only state in which anything can change
    completed  - finalized: ready for future invoicing, no longer editable
    cancelled  - voided; kept for the record, final

    Whether a transaction has been invoiced is a separate concern that the invoicing
    milestone will model as its own relationship, not as a lifecycle state.
    """

    DRAFT = "draft"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Transaction(TenantOwned, Numbered, Authored, Base):
    """The header of a sale: who is billed, and when. Industry-neutral.

    Anything specific to an industry (an animal, a project, a vehicle, a property...) is
    context attached later through the generic custom-field/reference mechanism; Sales
    does not know about it.
    """

    __tablename__ = "transactions"
    __table_args__ = (
        # Target for the tenant-safe composite foreign key from transaction_lines.
        UniqueConstraint("organization_id", "id", name="uq_transactions_organization_id_id"),
        UniqueConstraint("organization_id", "number", name="uq_transactions_organization_number"),
        # Target for modules whose records must agree with a transaction on its billing customer
        # AND currency (a referencing row with non-NULL values is then checked by PostgreSQL itself).
        UniqueConstraint(
            "organization_id", "id", "billing_customer_id", "currency", name="uq_transactions_org_id_customer_currency"
        ),
        ForeignKeyConstraint(
            ["organization_id", "billing_customer_id"],
            ["customers.organization_id", "customers.id"],
            ondelete="RESTRICT",
            name="fk_transactions_billing_customer_same_organization",
        ),
        CheckConstraint(
            "status IN ('" + "', '".join(TransactionStatus) + "')", name="ck_transactions_status"
        ),
        CheckConstraint("version >= 1 AND header_version >= 1", name="ck_transactions_versions_positive"),
        CheckConstraint("currency IS NULL OR currency ~ '^[A-Z]{3}$'", name="ck_transactions_currency_shape"),
        Index("ix_transactions_organization_status_date", "organization_id", "status", "transaction_date"),
        Index("ix_transactions_organization_billing_customer", "organization_id", "billing_customer_id"),
    )

    # The party that will be invoiced. One per transaction. Never inferred from, or
    # assumed equal to, any owner/context customer.
    billing_customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    transaction_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(
        String(16), default=TransactionStatus.DRAFT, server_default=text("'draft'")
    )
    # Optimistic concurrency (see app/modules/sales/versioning.py). `version` changes with
    # ANY change to the transaction: header, lines or status. `header_version` changes only
    # with the header (billing customer, date), so editing the header is not blocked by a
    # line that someone else changed.
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    header_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    # The currency the prices of this transaction are in, copied from the organization's default
    # AT CREATION. NULL for transactions that predate currencies (they are assigned one only by an
    # explicit action, never automatically). Once set it never changes (a database trigger enforces
    # it), whatever happens to the organization's setting.
    currency: Mapped[str | None] = mapped_column(String(3))


class TransactionLine(TenantOwned, Authored, Base):
    """One billable line. Item values are SNAPSHOTS copied at creation.

    `item_id` only links back to the catalog; editing the Item later never changes
    the description, unit, price, VAT or amounts stored here. `item_id` is nullable
    for ad-hoc lines. The three amounts are stored and kept consistent with the
    inputs by CHECK constraints (rounding rules: app/modules/sales/pricing.py).
    """

    __tablename__ = "transaction_lines"
    __table_args__ = (
        # Target of the composite foreign keys of whatever later refers to a line of a given
        # transaction (so the database can check that the line belongs to that transaction).
        UniqueConstraint("organization_id", "id", "transaction_id", name="uq_transaction_lines_org_id_transaction"),
        ForeignKeyConstraint(
            ["organization_id", "transaction_id"],
            ["transactions.organization_id", "transactions.id"],
            ondelete="CASCADE",  # only drafts are deletable (enforced by the API)
            name="fk_transaction_lines_transaction_same_organization",
        ),
        # MATCH SIMPLE: a NULL item_id (ad-hoc line) skips the check.
        ForeignKeyConstraint(
            ["organization_id", "item_id"],
            ["items.organization_id", "items.id"],
            ondelete="RESTRICT",
            name="fk_transaction_lines_item_same_organization",
        ),
        CheckConstraint("version >= 1", name="ck_transaction_lines_version_positive"),
        CheckConstraint("quantity > 0", name="ck_transaction_lines_quantity_positive"),
        CheckConstraint("unit_price_ex_vat >= 0", name="ck_transaction_lines_price_nonnegative"),
        CheckConstraint(
            "vat_rate >= 0 AND vat_rate <= 100", name="ck_transaction_lines_vat_rate_range"
        ),
        CheckConstraint(
            "net_amount = round(quantity * unit_price_ex_vat, 2)",
            name="ck_transaction_lines_net_amount",
        ),
        CheckConstraint(
            "vat_amount = round(net_amount * vat_rate / 100, 2)",
            name="ck_transaction_lines_vat_amount",
        ),
        CheckConstraint(
            "gross_amount = net_amount + vat_amount", name="ck_transaction_lines_gross_amount"
        ),
        *discount_constraints("transaction_lines"),
        CheckConstraint(
            "NOT priced_by_hand OR (catalog_discount_percent IS NULL AND customer_discount_percent IS NULL)",
            name="ck_transaction_lines_hand_price_layers",
        ),
        CheckConstraint("kind IN ('standard', 'service')", name="ck_transaction_lines_kind"),
        # A service is a catalog service performed at a time for a subject; other lines carry none of that.
        CheckConstraint(
            "(kind = 'service' AND item_id IS NOT NULL AND performed_at IS NOT NULL AND subject_type IS NOT NULL AND subject_id IS NOT NULL)"
            " OR (kind = 'standard' AND performed_at IS NULL AND performed_by IS NULL AND subject_type IS NULL AND subject_id IS NULL)",
            name="ck_transaction_lines_service_fields",
        ),
        Index("ix_transaction_lines_subject", "organization_id", "subject_type", "subject_id"),
        Index("ix_transaction_lines_organization_transaction", "organization_id", "transaction_id"),
        Index("ix_transaction_lines_organization_item", "organization_id", "item_id"),
    )

    transaction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    position: Mapped[int] = mapped_column(Integer)  # server-assigned print order
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))  # +1 per edit
    description: Mapped[str] = mapped_column(String(255))
    unit: Mapped[str] = mapped_column(String(32))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit_price_ex_vat: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    # Discount layers (see discount_constraints): the price before discounts and the two percentages, copied when the
    # line is priced from the catalog. NULL for ad-hoc lines and manually set prices.
    list_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    catalog_discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    customer_discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    # The line's own discount, set by a person on this line (the last layer). On a line priced by hand the typed price
    # is the list price it applies to.
    line_discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    # The price was typed by a person (an ad-hoc line or an overridden price): the catalog and customer layers never
    # apply, and a new billing customer or date does not reprice it.
    priced_by_hand: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    net_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    vat_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    # "standard": a catalog item or an ad-hoc line (told apart by item_id). "service": work performed for a subject
    # (a person, an animal...), always a catalog service, with when, by whom and for whom.
    kind: Mapped[str] = mapped_column(String(16), default="standard", server_default=text("'standard'"))
    performed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    performed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    # The subject as (registry key, id): Sales never knows what it is (see app.core.subjects).
    subject_type: Mapped[str | None] = mapped_column(String(64))
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    notes: Mapped[str | None] = mapped_column(Text)
