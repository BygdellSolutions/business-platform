"""the horse journal: an optional link to the order a visit was billed on

Only an id: orders belong to Sales, which answers for the number through a hook; the journal's text never flows into an invoice. A draft
order may be deleted later, so the id has no foreign key and a missing order just shows no number.

Revision ID: f3c5e7a9b1d2
Revises: e2b4d6f8a0c1
Create Date: 2026-10-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3c5e7a9b1d2"
down_revision: Union[str, Sequence[str], None] = "e2b4d6f8a0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("horse_journal_entries", sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    op.drop_column("horse_journal_entries", "order_id")
