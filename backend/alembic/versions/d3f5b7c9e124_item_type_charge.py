"""items: a third type, "charge" (travel, mileage, fees), next to service and product

Widens the `ck_items_type` CHECK. A charge never tracks stock (the existing `ck_items_track_stock_product` already
says so). The downgrade restores the narrow CHECK and therefore fails while any charge exists; nothing is deleted.

Revision ID: d3f5b7c9e124
Revises: c2e4a6b8d013
Create Date: 2026-10-10
"""

from typing import Sequence, Union

from alembic import op

revision: str = "d3f5b7c9e124"
down_revision: Union[str, Sequence[str], None] = "c2e4a6b8d013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_items_type", "items", type_="check")
    op.create_check_constraint("ck_items_type", "items", "type IN ('service', 'product', 'charge')")


def downgrade() -> None:
    op.drop_constraint("ck_items_type", "items", type_="check")
    op.create_check_constraint("ck_items_type", "items", "type IN ('service', 'product')")
