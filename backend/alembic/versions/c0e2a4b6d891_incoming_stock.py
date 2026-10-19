"""inventory: incoming stock and goods receipt

Additive: `incoming_stock` (quantity, received, expected date, supplier, reference, who and when, cancellation),
and `stock_movements.incoming_stock_id`: a receipt names the delivery it received, and only a receipt does
(no receipt movement exists before this revision).

Revision ID: c0e2a4b6d891
Revises: b9d1f3a5c780
Create Date: 2026-10-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c0e2a4b6d891"
down_revision: Union[str, Sequence[str], None] = "b9d1f3a5c780"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "incoming_stock",
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("received", sa.Numeric(12, 3), server_default=sa.text("0"), nullable=False),
        sa.Column("expected_on", sa.Date(), nullable=True),
        sa.Column("supplier", sa.String(length=255), nullable=True),
        sa.Column("reference", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("quantity > 0 AND received >= 0 AND received <= quantity", name="ck_incoming_stock_quantities"),
        sa.CheckConstraint("(cancelled_at IS NULL) = (cancelled_by IS NULL)", name="ck_incoming_stock_cancellation"),
        sa.ForeignKeyConstraint(["cancelled_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "item_id"], ["items.organization_id", "items.id"], name="fk_incoming_stock_item", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_incoming_stock_organization_id"), "incoming_stock", ["organization_id"])
    op.create_index(
        "ix_incoming_stock_open_item", "incoming_stock", ["organization_id", "item_id"], postgresql_where=sa.text("cancelled_at IS NULL AND received < quantity")
    )
    op.add_column("stock_movements", sa.Column("incoming_stock_id", sa.UUID(), nullable=True))
    op.create_check_constraint("ck_stock_movements_receipt_source", "stock_movements", "(reason = 'receipt') = (incoming_stock_id IS NOT NULL)")


def downgrade() -> None:
    op.drop_constraint("ck_stock_movements_receipt_source", "stock_movements", type_="check")
    op.drop_column("stock_movements", "incoming_stock_id")
    op.drop_index("ix_incoming_stock_open_item", table_name="incoming_stock")
    op.drop_index(op.f("ix_incoming_stock_organization_id"), table_name="incoming_stock")
    op.drop_table("incoming_stock")
