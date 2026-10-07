"""add the organization time zone

Revision ID: a1c3e5f7b902
Revises: e29c5d7a3b48
Create Date: 2026-10-08 02:00:00.000000

Nullable and never backfilled: an organization has no time zone until an owner or admin sets one, and until
then date defaults stay UTC exactly as before.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1c3e5f7b902"
down_revision: Union[str, Sequence[str], None] = "e29c5d7a3b48"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("timezone", sa.String(length=64), nullable=True))
    op.create_check_constraint(
        "ck_organizations_timezone_shape", "organizations", "timezone IS NULL OR timezone ~ '^[A-Za-z0-9_+/-]{1,64}$'"
    )


def downgrade() -> None:
    op.drop_constraint("ck_organizations_timezone_shape", "organizations", type_="check")
    op.drop_column("organizations", "timezone")
