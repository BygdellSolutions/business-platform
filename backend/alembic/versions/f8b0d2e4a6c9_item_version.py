"""items: a version for conflict detection (If-Match) on prices and the rest of the catalog record

Revision ID: f8b0d2e4a6c9
Revises: e7a9c1d3f5b8
Create Date: 2026-10-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f8b0d2e4a6c9"
down_revision: Union[str, Sequence[str], None] = "e7a9c1d3f5b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("items", sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False))
    op.create_check_constraint("ck_items_version_positive", "items", "version >= 1")


def downgrade() -> None:
    op.drop_constraint("ck_items_version_positive", "items", type_="check")
    op.drop_column("items", "version")
