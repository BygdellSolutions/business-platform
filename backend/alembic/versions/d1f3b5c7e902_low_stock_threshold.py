"""inventory: a low-stock threshold per item

Additive and nullable: no item gets a threshold until someone sets one.

Revision ID: d1f3b5c7e902
Revises: c0e2a4b6d891
Create Date: 2026-10-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d1f3b5c7e902"
down_revision: Union[str, Sequence[str], None] = "c0e2a4b6d891"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("items", sa.Column("low_stock_threshold", sa.Numeric(12, 3), nullable=True))
    op.create_check_constraint("ck_items_low_stock_threshold", "items", "low_stock_threshold IS NULL OR low_stock_threshold >= 0")


def downgrade() -> None:
    op.drop_constraint("ck_items_low_stock_threshold", "items", type_="check")
    op.drop_column("items", "low_stock_threshold")
