"""organizations: the registered office (säte) of a limited company, shown on its invoices

Revision ID: b2d4f6a8c0e1
Revises: a1c3e5f7b9d2
Create Date: 2026-10-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2d4f6a8c0e1"
down_revision: Union[str, Sequence[str], None] = "a1c3e5f7b9d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("registered_office", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "registered_office")
