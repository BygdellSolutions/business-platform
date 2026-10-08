import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Identity, Index, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base


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
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
