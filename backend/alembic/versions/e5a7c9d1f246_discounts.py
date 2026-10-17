"""discounts: customer permanent discount, temporary catalog discounts, discount layers on lines

Revision ID: e5a7c9d1f246
Revises: d4f6b8c0e135
Create Date: 2026-10-08 06:00:00.000000

Additive: existing lines keep NULL list prices (no discount is assumed for the past), existing customers have no
discount, and no item has a temporary discount.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e5a7c9d1f246"
down_revision: Union[str, Sequence[str], None] = "d4f6b8c0e135"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LINE_TABLES = ("transaction_lines", "invoice_lines")
LAYERS = (
    "(list_unit_price IS NULL AND catalog_discount_percent IS NULL AND customer_discount_percent IS NULL)"
    " OR (list_unit_price IS NOT NULL AND unit_price_ex_vat = round(round(list_unit_price"
    " * (100 - coalesce(catalog_discount_percent, 0)) / 100, 2) * (100 - coalesce(customer_discount_percent, 0)) / 100, 2))"
)


def upgrade() -> None:
    op.add_column("customers", sa.Column("default_discount_percent", sa.Numeric(5, 2), nullable=True))
    op.create_check_constraint(
        "ck_customers_default_discount_range", "customers",
        "default_discount_percent IS NULL OR (default_discount_percent > 0 AND default_discount_percent < 100)",
    )

    for table in LINE_TABLES:
        op.add_column(table, sa.Column("list_unit_price", sa.Numeric(12, 2), nullable=True))
        op.add_column(table, sa.Column("catalog_discount_percent", sa.Numeric(5, 2), nullable=True))
        op.add_column(table, sa.Column("customer_discount_percent", sa.Numeric(5, 2), nullable=True))
        for column, name in (("catalog_discount_percent", "catalog_discount_range"), ("customer_discount_percent", "customer_discount_range")):
            op.create_check_constraint(f"ck_{table}_{name}", table, f"{column} IS NULL OR ({column} > 0 AND {column} < 100)")
        op.create_check_constraint(f"ck_{table}_discount_layers", table, LAYERS)

    op.create_table(
        "item_discounts",
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("percent", sa.Numeric(5, 2), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.CheckConstraint("percent > 0 AND percent < 100", name="ck_item_discounts_percent_range"),
        sa.CheckConstraint("ends_on IS NULL OR ends_on >= starts_on", name="ck_item_discounts_period"),
        sa.ForeignKeyConstraint(["organization_id", "item_id"], ["items.organization_id", "items.id"], ondelete="CASCADE", name="fk_item_discounts_item"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT", name="fk_item_discounts_organization"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT", name="fk_item_discounts_created_by"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="RESTRICT", name="fk_item_discounts_updated_by"),
        sa.PrimaryKeyConstraint("id", name="pk_item_discounts"),
    )
    op.create_index("ix_item_discounts_item_period", "item_discounts", ["organization_id", "item_id", "starts_on"])
    op.create_index("ix_item_discounts_organization_id", "item_discounts", ["organization_id"])


def downgrade() -> None:
    op.drop_index("ix_item_discounts_organization_id", table_name="item_discounts")
    op.drop_index("ix_item_discounts_item_period", table_name="item_discounts")
    op.drop_table("item_discounts")
    for table in LINE_TABLES:
        op.drop_constraint(f"ck_{table}_discount_layers", table, type_="check")
        op.drop_constraint(f"ck_{table}_customer_discount_range", table, type_="check")
        op.drop_constraint(f"ck_{table}_catalog_discount_range", table, type_="check")
        op.drop_column(table, "customer_discount_percent")
        op.drop_column(table, "catalog_discount_percent")
        op.drop_column(table, "list_unit_price")
    op.drop_constraint("ck_customers_default_discount_range", "customers", type_="check")
    op.drop_column("customers", "default_discount_percent")
