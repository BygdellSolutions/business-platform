import uuid
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import TenantOwned


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


class Transaction(TenantOwned, Base):
    """The header of a sale: who is billed, and when. Industry-neutral.

    Anything specific to an industry (an animal, a project, a vehicle, a property...) is
    context attached later through the generic custom-field/reference mechanism; Sales
    does not know about it.
    """

    __tablename__ = "transactions"
    __table_args__ = (
        # Target for the tenant-safe composite foreign key from transaction_lines.
        UniqueConstraint("organization_id", "id", name="uq_transactions_organization_id_id"),
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


class TransactionLine(TenantOwned, Base):
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
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    net_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    vat_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
