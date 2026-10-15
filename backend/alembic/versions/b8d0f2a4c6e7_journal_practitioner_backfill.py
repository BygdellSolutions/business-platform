"""the horse journal: entries written before the practitioner was a member get theirs (the author, when the name says so)

Until the practitioner became a choice from the members list, an entry's practitioner was the author's name by default. Where that name
is still exactly the author's, the entry now points at the author; any other entry keeps its name and no member.

Revision ID: b8d0f2a4c6e7
Revises: a7c9e1b3d5f6
Create Date: 2026-10-15
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b8d0f2a4c6e7"
down_revision: Union[str, Sequence[str], None] = "a7c9e1b3d5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # finalized entries are frozen by a trigger; their practitioner id is filled in with the trigger off for this one statement
    op.execute("ALTER TABLE horse_journal_entries DISABLE TRIGGER trg_horse_journal_entries_final_immutable")
    op.execute(
        """
        UPDATE horse_journal_entries e SET practitioner_user_id = u.id
        FROM users u
        WHERE e.practitioner_user_id IS NULL AND u.id = e.created_by AND u.name = e.practitioner_name
        """
    )
    op.execute("ALTER TABLE horse_journal_entries ENABLE TRIGGER trg_horse_journal_entries_final_immutable")


def downgrade() -> None:
    pass  # the ids stay: they are true, and the name column was never changed
