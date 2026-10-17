"""incoming stock: a unit cost (what the organization pays per unit, excl. VAT)

Adds `incoming_stock.unit_cost` NUMERIC(12,2), nullable (older deliveries: not recorded), never negative.

Revision ID: c2e4a6b8d013
Revises: b1d3f5a7c902
Create Date: 2026-10-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c2e4a6b8d013"
down_revision: Union[str, Sequence[str], None] = "b1d3f5a7c902"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("incoming_stock", sa.Column("unit_cost", sa.Numeric(12, 2), nullable=True))
    op.create_check_constraint("ck_incoming_stock_unit_cost", "incoming_stock", "unit_cost IS NULL OR unit_cost >= 0")


def downgrade() -> None:
    op.drop_constraint("ck_incoming_stock_unit_cost", "incoming_stock", type_="check")
    op.drop_column("incoming_stock", "unit_cost")
