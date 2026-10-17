"""stock: an opening count of zero is recorded (a new product starts at 0)

The ledger refused every movement of zero. The first count of a product may be 0: it is recorded as an "opening" movement of 0
(before 0, after 0), which marks the product as counted. Every other reason still needs a real change.

Revision ID: d0f2a4c6e8b9
Revises: c9e1a3b5d7f8
Create Date: 2026-10-15
"""

from typing import Sequence, Union

from alembic import op

revision: str = "d0f2a4c6e8b9"
down_revision: Union[str, Sequence[str], None] = "c9e1a3b5d7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_stock_movements_change_nonzero", "stock_movements", type_="check")
    op.create_check_constraint("ck_stock_movements_change_nonzero", "stock_movements", "quantity_change <> 0 OR reason = 'opening'")


def downgrade() -> None:
    # The ledger is append-only: a zero opening count is never deleted to make this fit. If one exists, going back is refused.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM stock_movements WHERE quantity_change = 0) THEN "
        "RAISE EXCEPTION 'zero opening counts exist; they are never deleted, so this migration cannot be undone'; END IF; END $$"
    )
    op.drop_constraint("ck_stock_movements_change_nonzero", "stock_movements", type_="check")
    op.create_check_constraint("ck_stock_movements_change_nonzero", "stock_movements", "quantity_change <> 0")
