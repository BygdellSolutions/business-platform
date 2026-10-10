"""sales: a discount on each line, the last discount layer

Additive: `line_discount_percent` on transaction and invoice lines (0 < x < 100), and `transaction_lines.priced_by_hand`
(a typed price: no catalog or customer layer, and a new customer or date does not reprice it). Existing lines without
a list price were priced by hand or are ad-hoc; they are marked so. The discount-layer CHECK gains the last layer.

Revision ID: a4c6e8f0b235
Revises: f3b5d7e9a124
Create Date: 2026-10-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a4c6e8f0b235"
down_revision: Union[str, Sequence[str], None] = "f3b5d7e9a124"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_LAYERS = (
    "(list_unit_price IS NULL AND catalog_discount_percent IS NULL AND customer_discount_percent IS NULL)"
    " OR (list_unit_price IS NOT NULL AND unit_price_ex_vat = round(round(list_unit_price"
    " * (100 - coalesce(catalog_discount_percent, 0)) / 100, 2) * (100 - coalesce(customer_discount_percent, 0)) / 100, 2))"
)
NEW_LAYERS = (
    "(list_unit_price IS NULL AND catalog_discount_percent IS NULL AND customer_discount_percent IS NULL AND line_discount_percent IS NULL)"
    " OR (list_unit_price IS NOT NULL AND unit_price_ex_vat = round(round(round(list_unit_price * (100 - coalesce(catalog_discount_percent, 0)) / 100, 2) * (100 - coalesce(customer_discount_percent, 0)) / 100, 2) * (100 - coalesce(line_discount_percent, 0)) / 100, 2))"
)


def upgrade() -> None:
    for table in ("transaction_lines", "invoice_lines"):
        op.add_column(table, sa.Column("line_discount_percent", sa.Numeric(5, 2), nullable=True))
        op.create_check_constraint(f"ck_{table}_line_discount_range", table, "line_discount_percent IS NULL OR (line_discount_percent > 0 AND line_discount_percent < 100)")
    op.add_column("transaction_lines", sa.Column("priced_by_hand", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.execute("UPDATE transaction_lines SET priced_by_hand = true WHERE list_unit_price IS NULL")
    op.create_check_constraint(
        "ck_transaction_lines_hand_price_layers", "transaction_lines", "NOT priced_by_hand OR (catalog_discount_percent IS NULL AND customer_discount_percent IS NULL)"
    )
    # Issued invoice lines are protected by a trigger, but replacing a CHECK touches no row.
    for table in ("transaction_lines", "invoice_lines"):
        op.drop_constraint(f"ck_{table}_discount_layers", table, type_="check")
        op.create_check_constraint(f"ck_{table}_discount_layers", table, NEW_LAYERS)


def downgrade() -> None:
    for table in ("transaction_lines", "invoice_lines"):
        op.drop_constraint(f"ck_{table}_discount_layers", table, type_="check")
        op.create_check_constraint(f"ck_{table}_discount_layers", table, OLD_LAYERS)
    op.drop_constraint("ck_transaction_lines_hand_price_layers", "transaction_lines", type_="check")
    op.drop_column("transaction_lines", "priced_by_hand")
    for table in ("transaction_lines", "invoice_lines"):
        op.drop_constraint(f"ck_{table}_line_discount_range", table, type_="check")
        op.drop_column(table, "line_discount_percent")
