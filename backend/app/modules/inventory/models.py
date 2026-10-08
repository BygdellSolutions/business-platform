import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Identity, Index, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base
from app.models.mixins import TenantOwned


class MovementReason(StrEnum):
    OPENING = "opening"  # the first count of an item's stock
    ADJUSTMENT = "adjustment"  # a correction after a count, damage, loss... (a note says why)
    RECEIPT = "receipt"  # goods received (slice I4)
    DELIVERY = "delivery"  # handed over when a transaction is completed (slice I3)
    RETURN = "return"  # a delivery undone by a reopen or a cancel (slice I3)


class StockMovement(Base):
    """One change of an item's physical stock: the ledger that explains every on-hand figure.

    Append-only (a trigger refuses UPDATE and DELETE, except while the organization itself is being
    deleted): a mistake is corrected by another movement, never by editing one. Each row carries the
    quantity before and after, and the CHECKs keep them consistent and never negative; `sequence`
    orders an item's movements, so the latest row's `quantity_after` IS the item's on-hand quantity.
    Every writer holds the item's row lock (`app.modules.inventory.service.lock_items`), so two
    movements of one item can never be computed from the same "before".

    `transaction_id` / `transaction_line_id` say which sale a delivery or return belongs to. They are
    plain ids on purpose: the ledger outlives a draft line that is later deleted.
    """

    __tablename__ = "stock_movements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "item_id"], ["items.organization_id", "items.id"], ondelete="RESTRICT", name="fk_stock_movements_item"
        ),
        CheckConstraint("quantity_change <> 0", name="ck_stock_movements_change_nonzero"),
        CheckConstraint("quantity_before >= 0 AND quantity_after >= 0", name="ck_stock_movements_never_negative"),
        CheckConstraint("quantity_after = quantity_before + quantity_change", name="ck_stock_movements_arithmetic"),
        CheckConstraint("reason IN ('" + "', '".join(MovementReason) + "')", name="ck_stock_movements_reason"),
        CheckConstraint("reason <> 'adjustment' OR note IS NOT NULL", name="ck_stock_movements_adjustment_note"),
        # Goods come in only through a recorded incoming delivery that a person received.
        CheckConstraint("(reason = 'receipt') = (incoming_stock_id IS NOT NULL)", name="ck_stock_movements_receipt_source"),
        Index("ix_stock_movements_item_sequence", "organization_id", "item_id", "sequence"),
        Index("ix_stock_movements_transaction", "organization_id", "transaction_id", postgresql_where=text("transaction_id IS NOT NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    sequence: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True)
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    quantity_change: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    quantity_before: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    quantity_after: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    reason: Mapped[str] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(String(255))
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    transaction_line_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # For a receipt: the incoming delivery it received (a plain id, like the transaction ids).
    incoming_stock_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LineFulfillment(TenantOwned, Base):
    """What became of one stock-tracking line when its transaction was completed.

    `ordered` = `delivered` (handed over at completion, a delivery movement) + `backordered` (the shortage, waiting
    for stock). `fulfilled_later` grows as backordered units are delivered afterwards (I5); the backorder is open
    while it is below `backordered`. A reopen or a cancel never deletes the row: it is CANCELLED (when, by whom,
    why) after everything delivered for it has come back through return movements, and a later completion writes a
    new row. Only one row per line is active (not cancelled) at a time.

    Open backorders are units already promised: "available" stock is on hand minus what they still wait for.
    """

    __tablename__ = "line_fulfillments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "item_id"], ["items.organization_id", "items.id"], ondelete="RESTRICT", name="fk_line_fulfillments_item"
        ),
        CheckConstraint("ordered > 0 AND delivered >= 0 AND backordered >= 0", name="ck_line_fulfillments_quantities"),
        CheckConstraint("ordered = delivered + backordered", name="ck_line_fulfillments_split"),
        CheckConstraint("fulfilled_later >= 0 AND fulfilled_later <= backordered", name="ck_line_fulfillments_fulfilled_later"),
        CheckConstraint(
            "(cancelled_at IS NULL AND cancel_reason IS NULL) OR (cancelled_at IS NOT NULL AND cancel_reason IN ('reopen', 'cancel'))",
            name="ck_line_fulfillments_cancellation",
        ),
        Index(
            "uq_line_fulfillments_active_line", "organization_id", "transaction_line_id", unique=True, postgresql_where=text("cancelled_at IS NULL")
        ),
        Index("ix_line_fulfillments_transaction", "organization_id", "transaction_id"),
        Index(
            "ix_line_fulfillments_open_item",
            "organization_id",
            "item_id",
            "created_at",
            postgresql_where=text("cancelled_at IS NULL AND fulfilled_later < backordered"),
        ),
    )

    transaction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    transaction_line_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    ordered: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    delivered: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    backordered: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    fulfilled_later: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("0"), server_default=text("0"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    cancel_reason: Mapped[str | None] = mapped_column(String(16))


class IncomingStock(TenantOwned, Base):
    """Stock on its way: ordered from a supplier, not yet on the shelf.

    Incoming is never on hand. A person receives it (all or part; `received` grows), and each receipt is a
    `receipt` movement in the ledger. What is left can be cancelled (the delivery will not come); units already
    received stay. A receipt never fulfills a backorder by itself: it makes waiting backorders "ready to fulfill",
    and a person confirms who gets what (I5).
    """

    __tablename__ = "incoming_stock"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "item_id"], ["items.organization_id", "items.id"], ondelete="RESTRICT", name="fk_incoming_stock_item"
        ),
        CheckConstraint("quantity > 0 AND received >= 0 AND received <= quantity", name="ck_incoming_stock_quantities"),
        CheckConstraint("(cancelled_at IS NULL) = (cancelled_by IS NULL)", name="ck_incoming_stock_cancellation"),
        Index("ix_incoming_stock_open_item", "organization_id", "item_id", postgresql_where=text("cancelled_at IS NULL AND received < quantity")),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    received: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("0"), server_default=text("0"))
    expected_on: Mapped[date | None] = mapped_column(Date)
    supplier: Mapped[str | None] = mapped_column(String(255))
    reference: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
