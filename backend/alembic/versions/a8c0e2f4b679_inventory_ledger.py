"""inventory: article numbers, stock tracking and the stock movement ledger

Additive. Items gain `sku` (optional, unique per organization) and `track_stock` (products only;
every existing item starts without it). `stock_movements` is the append-only ledger of physical
stock: a trigger refuses UPDATE and DELETE except while its own organization is being deleted
(the same escape as the history).

Revision ID: a8c0e2f4b679
Revises: f7b9d1e3a568
Create Date: 2026-10-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a8c0e2f4b679"
down_revision: Union[str, Sequence[str], None] = "f7b9d1e3a568"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("items", sa.Column("sku", sa.String(length=64), nullable=True))
    op.add_column("items", sa.Column("track_stock", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.create_check_constraint("ck_items_track_stock_product", "items", "NOT track_stock OR type = 'product'")
    op.create_check_constraint("ck_items_sku_not_blank", "items", "sku IS NULL OR length(btrim(sku)) > 0")
    op.create_index("uq_items_organization_sku", "items", ["organization_id", "sku"], unique=True, postgresql_where=sa.text("sku IS NOT NULL"))

    op.create_table(
        "stock_movements",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("quantity_change", sa.Numeric(12, 3), nullable=False),
        sa.Column("quantity_before", sa.Numeric(12, 3), nullable=False),
        sa.Column("quantity_after", sa.Numeric(12, 3), nullable=False),
        sa.Column("reason", sa.String(length=16), nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("transaction_id", sa.UUID(), nullable=True),
        sa.Column("transaction_line_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("quantity_change <> 0", name="ck_stock_movements_change_nonzero"),
        sa.CheckConstraint("quantity_before >= 0 AND quantity_after >= 0", name="ck_stock_movements_never_negative"),
        sa.CheckConstraint("quantity_after = quantity_before + quantity_change", name="ck_stock_movements_arithmetic"),
        sa.CheckConstraint("reason IN ('opening', 'adjustment', 'receipt', 'delivery', 'return')", name="ck_stock_movements_reason"),
        sa.CheckConstraint("reason <> 'adjustment' OR note IS NOT NULL", name="ck_stock_movements_adjustment_note"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "item_id"], ["items.organization_id", "items.id"], name="fk_stock_movements_item", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sequence"),
    )
    op.create_index(op.f("ix_stock_movements_organization_id"), "stock_movements", ["organization_id"])
    op.create_index("ix_stock_movements_item_sequence", "stock_movements", ["organization_id", "item_id", "sequence"])
    op.create_index(
        "ix_stock_movements_transaction", "stock_movements", ["organization_id", "transaction_id"], postgresql_where=sa.text("transaction_id IS NOT NULL")
    )
    op.execute(
        """
        CREATE FUNCTION stock_movements_append_only() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               AND current_setting('app.deleting_organization', true) = OLD.organization_id::text THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'stock movements are append-only' USING ERRCODE = 'check_violation';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_stock_movements_append_only
        BEFORE UPDATE OR DELETE ON stock_movements
        FOR EACH ROW EXECUTE FUNCTION stock_movements_append_only()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_stock_movements_append_only ON stock_movements")
    op.execute("DROP FUNCTION stock_movements_append_only()")
    op.drop_index("ix_stock_movements_transaction", table_name="stock_movements")
    op.drop_index("ix_stock_movements_item_sequence", table_name="stock_movements")
    op.drop_index(op.f("ix_stock_movements_organization_id"), table_name="stock_movements")
    op.drop_table("stock_movements")
    op.drop_index("uq_items_organization_sku", table_name="items")
    op.drop_constraint("ck_items_sku_not_blank", "items", type_="check")
    op.drop_constraint("ck_items_track_stock_product", "items", type_="check")
    op.drop_column("items", "track_stock")
    op.drop_column("items", "sku")
