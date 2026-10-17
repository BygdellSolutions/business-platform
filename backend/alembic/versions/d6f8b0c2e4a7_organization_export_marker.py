"""organizations: when the owner last downloaded a copy of the organization's data

Additive: one nullable column. Deleting an organization is permanent and keeps nothing, so deletion asks the owner either to have
downloaded a copy in the last day or to say explicitly that they want none; this column is what "downloaded a copy" is checked against
(set when a complete export has been streamed to the end).

Revision ID: d6f8b0c2e4a7
Revises: c5e7a9b1d3f4
Create Date: 2026-10-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d6f8b0c2e4a7"
down_revision: Union[str, Sequence[str], None] = "c5e7a9b1d3f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("last_export_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "last_export_at")
