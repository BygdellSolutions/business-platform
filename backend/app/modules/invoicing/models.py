"""Invoicing tables (see docs/architecture.md, "Invoicing design").

An invoice is a DOCUMENT: once issued, everything needed to render it is stored here, so it
never depends on the live customer, organization, item or custom-field records. Source links
(`customer_id`, `source_transaction_id`, `source_line_id`) are metadata for navigation and for
protecting the sources from deletion; they never supply document text or amounts.

Every reference to another tenant-owned table is a composite (organization_id, ...) key, so
PostgreSQL itself refuses a link across organizations. Issued invoices and their children are
made immutable by invoice-local triggers (created in the migration); a trigger here knows only
these tables.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    LargeBinary,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy import event
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base
from app.models.mixins import Authored, TenantOwned
from app.modules.sales.models import discount_constraints

DEFAULT_SERIES = "default"
SNAPSHOT_SCHEMA = 1  # version of the customer snapshot structure
ISSUER_SNAPSHOT_SCHEMA = 2  # version of the issuer snapshot: 2 added contact, payment, F-tax and document language


class InvoiceStatus(StrEnum):
    DRAFT = "draft"  # reserves its transactions; has no number; header data editable
    ISSUED = "issued"  # numbered and frozen


class Invoice(TenantOwned, Authored, Base):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_invoices_organization_id_id"),
        # Target of invoice_transactions: one customer and one currency per invoice, enforced by PostgreSQL.
        UniqueConstraint(
            "organization_id", "id", "customer_id", "currency", name="uq_invoices_org_id_customer_currency"
        ),
        # THE numbering uniqueness. number_text is a stored label and is deliberately not unique.
        UniqueConstraint("organization_id", "series", "number", name="uq_invoices_organization_series_number"),
        ForeignKeyConstraint(
            ["organization_id", "customer_id"],
            ["customers.organization_id", "customers.id"],
            ondelete="RESTRICT",
            name="fk_invoices_customer_same_organization",
        ),
        CheckConstraint("status IN ('draft', 'issued')", name="ck_invoices_status"),
        CheckConstraint("version >= 1", name="ck_invoices_version_positive"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_invoices_currency_shape"),
        CheckConstraint("series <> ''", name="ck_invoices_series_not_empty"),
        CheckConstraint("due_date IS NULL OR due_date >= invoice_date", name="ck_invoices_due_after_invoice_date"),
        CheckConstraint("number IS NULL OR number >= 1", name="ck_invoices_number_positive"),
        CheckConstraint(
            "status <> 'issued' OR (number IS NOT NULL AND number_text IS NOT NULL "
            "AND issued_at IS NOT NULL AND issued_by IS NOT NULL)",
            name="ck_invoices_issued_has_number",
        ),
        CheckConstraint(
            "status = 'issued' OR (number IS NULL AND number_text IS NULL "
            "AND issued_at IS NULL AND issued_by IS NULL)",
            name="ck_invoices_draft_has_no_number",
        ),
        CheckConstraint(
            "net_amount >= 0 AND vat_amount >= 0 AND gross_amount = net_amount + vat_amount",
            name="ck_invoices_totals",
        ),
        CheckConstraint(
            "jsonb_typeof(customer_snapshot) = 'object' AND jsonb_typeof(issuer_snapshot) = 'object'",
            name="ck_invoices_snapshots_are_objects",
        ),
        Index("ix_invoices_organization_status_date", "organization_id", "status", "invoice_date"),
        Index("ix_invoices_organization_customer", "organization_id", "customer_id"),
    )

    status: Mapped[str] = mapped_column(String(16), default=InvoiceStatus.DRAFT, server_default=text("'draft'"))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))  # If-Match
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    currency: Mapped[str] = mapped_column(String(3))  # NOT NULL: a currency-less transaction is not invoiceable
    # Versioned copies of the buyer and the seller (see snapshots.py). Re-taken at issuance.
    customer_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    issuer_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    customer_name: Mapped[str] = mapped_column(String(255))  # copy of the snapshot's name, for lists and search
    invoice_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(String(2000))
    series: Mapped[str] = mapped_column(String(32), default=DEFAULT_SERIES, server_default=text("'default'"))
    number: Mapped[int | None] = mapped_column(BigInteger)
    number_text: Mapped[str | None] = mapped_column(String(64))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    # Stored, never recalculated: the sums of the stored line amounts.
    net_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    vat_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))


class InvoiceTransaction(TenantOwned, Base):
    """A source transaction of an invoice. A row here IS the fact that the transaction is
    reserved (draft invoice) or invoiced (issued invoice); Sales stores nothing about it."""

    __tablename__ = "invoice_transactions"
    __table_args__ = (
        # The final guard against invoicing a transaction twice, even for two simultaneous creators.
        UniqueConstraint("organization_id", "transaction_id", name="uq_invoice_transactions_source_once"),
        UniqueConstraint(
            "organization_id", "invoice_id", "transaction_id", name="uq_invoice_transactions_org_invoice_transaction"
        ),
        UniqueConstraint("organization_id", "invoice_id", "position", name="uq_invoice_transactions_position"),
        ForeignKeyConstraint(
            ["organization_id", "invoice_id", "customer_id", "currency"],
            ["invoices.organization_id", "invoices.id", "invoices.customer_id", "invoices.currency"],
            ondelete="CASCADE",
            name="fk_invoice_transactions_invoice_customer_currency",
        ),
        # The transaction must have this billing customer and this currency (currency is NOT NULL
        # here, so a transaction without a currency can never match).
        ForeignKeyConstraint(
            ["organization_id", "transaction_id", "customer_id", "currency"],
            [
                "transactions.organization_id",
                "transactions.id",
                "transactions.billing_customer_id",
                "transactions.currency",
            ],
            ondelete="RESTRICT",
            name="fk_invoice_transactions_transaction_customer_currency",
        ),
        CheckConstraint("position >= 1 AND source_version >= 1", name="ck_invoice_transactions_positive"),
        CheckConstraint("jsonb_typeof(fields) = 'array'", name="ck_invoice_transactions_fields_array"),
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    transaction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    currency: Mapped[str] = mapped_column(String(3))
    transaction_date: Mapped[date] = mapped_column(Date)
    # The transaction's version when it was reserved: issuing re-checks it (defense in depth).
    source_version: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer)
    # Custom fields flagged for invoices, as they were (generic list; see snapshots.py).
    fields: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))


class InvoiceLine(TenantOwned, Base):
    """A copy of one Sales line: the same inputs and the three stored amounts, verbatim."""

    __tablename__ = "invoice_lines"
    __table_args__ = (
        UniqueConstraint("organization_id", "source_line_id", name="uq_invoice_lines_source_line_once"),
        UniqueConstraint("organization_id", "invoice_id", "position", name="uq_invoice_lines_position"),
        ForeignKeyConstraint(
            ["organization_id", "invoice_id", "source_transaction_id"],
            [
                "invoice_transactions.organization_id",
                "invoice_transactions.invoice_id",
                "invoice_transactions.transaction_id",
            ],
            ondelete="CASCADE",
            name="fk_invoice_lines_invoice_transaction",
        ),
        ForeignKeyConstraint(
            ["organization_id", "source_line_id", "source_transaction_id"],
            [
                "transaction_lines.organization_id",
                "transaction_lines.id",
                "transaction_lines.transaction_id",
            ],
            ondelete="RESTRICT",
            name="fk_invoice_lines_source_line",
        ),
        CheckConstraint("position >= 1", name="ck_invoice_lines_position_positive"),
        # The same consistency rules as transaction_lines (the amounts are copies, not calculations).
        CheckConstraint("quantity > 0", name="ck_invoice_lines_quantity_positive"),
        CheckConstraint("unit_price_ex_vat >= 0", name="ck_invoice_lines_price_nonnegative"),
        CheckConstraint("vat_rate >= 0 AND vat_rate <= 100", name="ck_invoice_lines_vat_rate_range"),
        CheckConstraint("net_amount = round(quantity * unit_price_ex_vat, 2)", name="ck_invoice_lines_net_amount"),
        CheckConstraint("vat_amount = round(net_amount * vat_rate / 100, 2)", name="ck_invoice_lines_vat_amount"),
        CheckConstraint("gross_amount = net_amount + vat_amount", name="ck_invoice_lines_gross_amount"),
        CheckConstraint("jsonb_typeof(fields) = 'array'", name="ck_invoice_lines_fields_array"),
        CheckConstraint("service IS NULL OR jsonb_typeof(service) = 'object'", name="ck_invoice_lines_service_object"),
        *discount_constraints("invoice_lines"),
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    source_transaction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    source_line_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    position: Mapped[int] = mapped_column(Integer)  # 1..n across the whole invoice
    description: Mapped[str] = mapped_column(String(255))
    unit: Mapped[str] = mapped_column(String(32))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit_price_ex_vat: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    # Discount layers (see discount_constraints): the price before discounts and the two percentages, copied when the
    # line is priced from the catalog. NULL for ad-hoc lines and manually set prices.
    list_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    catalog_discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    customer_discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    line_discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    net_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    vat_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    fields: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # For a service line: when, by whom and for whom, as shown at invoicing (a snapshot, never resolved again).
    service: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))  # None is SQL NULL, not JSON null


class InvoiceVatRow(TenantOwned, Base):
    """The stored VAT breakdown: the sums of the stored line amounts per VAT rate."""

    __tablename__ = "invoice_vat_rows"
    __table_args__ = (
        UniqueConstraint("organization_id", "invoice_id", "vat_rate", name="uq_invoice_vat_rows_rate"),
        ForeignKeyConstraint(
            ["organization_id", "invoice_id"],
            ["invoices.organization_id", "invoices.id"],
            ondelete="CASCADE",
            name="fk_invoice_vat_rows_invoice_same_organization",
        ),
        CheckConstraint("vat_rate >= 0 AND vat_rate <= 100", name="ck_invoice_vat_rows_rate_range"),
        CheckConstraint("net_amount >= 0 AND vat_amount >= 0", name="ck_invoice_vat_rows_nonnegative"),
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    net_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    vat_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))


class InvoiceCounter(Base):
    """The next number to hand out per organization and series.

    Changed only inside the transaction that issues an invoice, so a failed issuance rolls
    the counter back with everything else and an issued number is never handed out again.
    """

    __tablename__ = "invoice_counters"
    __table_args__ = (
        PrimaryKeyConstraint("organization_id", "series", name="pk_invoice_counters"),
        CheckConstraint("next_number >= 1", name="ck_invoice_counters_next_positive"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"))
    series: Mapped[str] = mapped_column(String(32))
    next_number: Mapped[int] = mapped_column(BigInteger)


class InvoicePdf(TenantOwned, Base):
    """A frozen PDF of an issued invoice, per template version: rendered once and never changed.

    One per invoice AND template version (owner decision 2026-10-08: the current template is used at all times). A
    download serves the artifact of the current template, creating it on first need; artifacts of older templates stay
    stored, unchanged, as history. Never updated or deleted (a trigger refuses both) and only an ISSUED invoice can
    have one. `source_sha256` is the hash of the canonical document the PDF was rendered from, so the
    artifact can always be checked against the immutable invoice; `renderer` and `template_version`
    say what produced it. The bytes live here (not in a file store) so they are backed up, restored and
    tenant-protected exactly like the invoice they belong to.
    """

    __tablename__ = "invoice_pdfs"
    __table_args__ = (
        UniqueConstraint("organization_id", "invoice_id", "template_version", name="uq_invoice_pdfs_one_per_template"),
        ForeignKeyConstraint(
            ["organization_id", "invoice_id"],
            ["invoices.organization_id", "invoices.id"],
            ondelete="RESTRICT",
            name="fk_invoice_pdfs_invoice_same_organization",
        ),
        CheckConstraint("byte_size > 0 AND byte_size = octet_length(content)", name="ck_invoice_pdfs_byte_size"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$' AND source_sha256 ~ '^[0-9a-f]{64}$'", name="ck_invoice_pdfs_hash_shape"),
        CheckConstraint("sha256 = encode(sha256(content), 'hex')", name="ck_invoice_pdfs_sha256_matches"),
        CheckConstraint("substring(content from 1 for 5) = decode('255044462d', 'hex')", name="ck_invoice_pdfs_is_pdf"),
        CheckConstraint("template_version >= 1 AND renderer <> ''", name="ck_invoice_pdfs_provenance"),
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    renderer: Mapped[str] = mapped_column(String(255))
    template_version: Mapped[int] = mapped_column(Integer)
    source_sha256: Mapped[str] = mapped_column(String(64))


@event.listens_for(InvoicePdf, "before_update")
@event.listens_for(InvoicePdf, "before_delete")
def _frozen_artifact(mapper, connection, target) -> None:
    """Application-level guard (the database trigger is the one that cannot be bypassed): the ORM
    never changes or removes a stored PDF, so no code path can do it by accident."""
    raise ValueError("an invoice PDF is a frozen artifact and cannot be changed or deleted")


PAYMENT_METHODS = ("bankgiro", "plusgiro", "bank_transfer", "swish", "card", "cash", "other")


class InvoicePayment(TenantOwned, Base):
    """A payment received for an ISSUED invoice, recorded by a person (no bank import yet).

    Append-only (a trigger refuses UPDATE and DELETE, except while the organization is being deleted): a payment
    recorded by mistake is cancelled by a REVERSAL, a row with the negative amount that names the payment it reverses
    (each payment can be reversed once). What is paid is the sum of an invoice's rows; it never exceeds the invoice's
    gross amount (checked under the invoice's row lock). Amounts are in the invoice's currency.
    """

    __tablename__ = "invoice_payments"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_invoice_payments_organization_id_id"),
        ForeignKeyConstraint(
            ["organization_id", "invoice_id"], ["invoices.organization_id", "invoices.id"], ondelete="RESTRICT", name="fk_invoice_payments_invoice"
        ),
        ForeignKeyConstraint(
            ["organization_id", "reverses_payment_id"],
            ["invoice_payments.organization_id", "invoice_payments.id"],
            ondelete="RESTRICT",
            name="fk_invoice_payments_reverses",
        ),
        CheckConstraint(
            "(reverses_payment_id IS NULL AND amount > 0) OR (reverses_payment_id IS NOT NULL AND amount < 0)", name="ck_invoice_payments_amount_sign"
        ),
        CheckConstraint("method IN ('" + "', '".join(PAYMENT_METHODS) + "')", name="ck_invoice_payments_method"),
        Index("uq_invoice_payments_reversed_once", "organization_id", "reverses_payment_id", unique=True, postgresql_where=text("reverses_payment_id IS NOT NULL")),
        Index("ix_invoice_payments_invoice", "organization_id", "invoice_id"),
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    paid_on: Mapped[date] = mapped_column(Date)
    method: Mapped[str] = mapped_column(String(16))
    reference: Mapped[str | None] = mapped_column(String(255))
    note: Mapped[str | None] = mapped_column(String(500))
    reverses_payment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
