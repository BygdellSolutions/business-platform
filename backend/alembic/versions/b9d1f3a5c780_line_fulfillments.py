"""inventory: what completion delivered and backordered per stock-tracking line

Additive: `line_fulfillments` (ordered = delivered + backordered, fulfilled later, cancellation by reopen or
cancel; one active row per line). Existing completed transactions get no rows: their stock was never tracked.

Revision ID: b9d1f3a5c780
Revises: a8c0e2f4b679
Create Date: 2026-10-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b9d1f3a5c780"
down_revision: Union[str, Sequence[str], None] = "a8c0e2f4b679"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "line_fulfillments",
        sa.Column("transaction_id", sa.UUID(), nullable=False),
        sa.Column("transaction_line_id", sa.UUID(), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("ordered", sa.Numeric(12, 3), nullable=False),
        sa.Column("delivered", sa.Numeric(12, 3), nullable=False),
        sa.Column("backordered", sa.Numeric(12, 3), nullable=False),
        sa.Column("fulfilled_later", sa.Numeric(12, 3), server_default=sa.text("0"), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", sa.UUID(), nullable=True),
        sa.Column("cancel_reason", sa.String(length=16), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("ordered > 0 AND delivered >= 0 AND backordered >= 0", name="ck_line_fulfillments_quantities"),
        sa.CheckConstraint("ordered = delivered + backordered", name="ck_line_fulfillments_split"),
        sa.CheckConstraint("fulfilled_later >= 0 AND fulfilled_later <= backordered", name="ck_line_fulfillments_fulfilled_later"),
        sa.CheckConstraint(
            "(cancelled_at IS NULL AND cancel_reason IS NULL) OR (cancelled_at IS NOT NULL AND cancel_reason IN ('reopen', 'cancel'))",
            name="ck_line_fulfillments_cancellation",
        ),
        sa.ForeignKeyConstraint(["cancelled_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "item_id"], ["items.organization_id", "items.id"], name="fk_line_fulfillments_item", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_line_fulfillments_organization_id"), "line_fulfillments", ["organization_id"])
    op.create_index("ix_line_fulfillments_transaction", "line_fulfillments", ["organization_id", "transaction_id"])
    op.create_index(
        "uq_line_fulfillments_active_line", "line_fulfillments", ["organization_id", "transaction_line_id"], unique=True, postgresql_where=sa.text("cancelled_at IS NULL")
    )
    op.create_index(
        "ix_line_fulfillments_open_item",
        "line_fulfillments",
        ["organization_id", "item_id", "created_at"],
        postgresql_where=sa.text("cancelled_at IS NULL AND fulfilled_later < backordered"),
    )


def downgrade() -> None:
    op.drop_index("ix_line_fulfillments_open_item", table_name="line_fulfillments")
    op.drop_index("uq_line_fulfillments_active_line", table_name="line_fulfillments")
    op.drop_index("ix_line_fulfillments_transaction", table_name="line_fulfillments")
    op.drop_index(op.f("ix_line_fulfillments_organization_id"), table_name="line_fulfillments")
    op.drop_table("line_fulfillments")
