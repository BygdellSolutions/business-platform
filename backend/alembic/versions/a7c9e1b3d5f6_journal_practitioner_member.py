"""the horse journal: the practitioner is a member of the organization, chosen from a list (the name is kept as written at the time)

Revision ID: a7c9e1b3d5f6
Revises: f6b8d0e2a4c3
Create Date: 2026-10-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7c9e1b3d5f6"
down_revision: Union[str, Sequence[str], None] = "f6b8d0e2a4c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("horse_journal_entries", sa.Column("practitioner_user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_horse_journal_entries_practitioner", "horse_journal_entries", "users", ["practitioner_user_id"], ["id"], ondelete="RESTRICT")


def downgrade() -> None:
    op.drop_constraint("fk_horse_journal_entries_practitioner", "horse_journal_entries", type_="foreignkey")
    op.drop_column("horse_journal_entries", "practitioner_user_id")
