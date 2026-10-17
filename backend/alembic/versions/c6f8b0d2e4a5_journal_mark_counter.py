"""the horse journal: a mark number is never given to another mark of the entry, even after the mark that had it is deleted

`next_mark_number` is the number the next new mark of the entry gets: one past the highest number ever handed out (the entry's marks
only show the numbers still in use, so a deleted last mark's number would be given again, and a note saying "see mark 3" would point at
a different mark). Existing entries start one past their highest number in use; a finalized entry is frozen by a trigger, so it is
switched off for the one statement that fills the column in.

Revision ID: c6f8b0d2e4a5
Revises: b5e7a9c1d3f4
Create Date: 2026-10-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c6f8b0d2e4a5"
down_revision: Union[str, Sequence[str], None] = "b5e7a9c1d3f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "horse_journal_entries"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("next_mark_number", sa.Integer(), server_default=sa.text("1"), nullable=False))
    op.execute(f"ALTER TABLE {TABLE} DISABLE TRIGGER trg_horse_journal_entries_final_immutable")
    op.execute(
        f"""
        UPDATE {TABLE} SET next_mark_number = 1 + coalesce(
            (SELECT max((mark ->> 'number')::int) FROM jsonb_array_elements(marks) AS mark WHERE mark ->> 'number' IS NOT NULL), 0)
        """
    )
    op.execute(f"ALTER TABLE {TABLE} ENABLE TRIGGER trg_horse_journal_entries_final_immutable")
    op.create_check_constraint(f"ck_{TABLE}_next_mark_number", TABLE, "next_mark_number >= 1")


def downgrade() -> None:
    op.drop_constraint(f"ck_{TABLE}_next_mark_number", TABLE, type_="check")
    op.drop_column(TABLE, "next_mark_number")
