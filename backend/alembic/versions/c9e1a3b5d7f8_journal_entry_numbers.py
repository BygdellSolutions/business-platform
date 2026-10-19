"""the horse journal: a number per organization for every entry (like horses and customers have), for people to refer to

Entries get 1, 2, 3 ... per organization in the order they were created, handed out by the same trigger as the other records
(`assign_record_number`, counter series `horse_journal_entries`). Finalized entries are frozen by a trigger, so it is switched off
for the one statement that numbers the entries that already exist.

Revision ID: c9e1a3b5d7f8
Revises: b8d0f2a4c6e7
Create Date: 2026-10-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9e1a3b5d7f8"
down_revision: Union[str, Sequence[str], None] = "b8d0f2a4c6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "horse_journal_entries"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("number", sa.BigInteger(), nullable=True))
    op.execute(f"ALTER TABLE {TABLE} DISABLE TRIGGER trg_horse_journal_entries_final_immutable")
    op.execute(
        f"""
        UPDATE {TABLE} t SET number = n.number
        FROM (SELECT id, row_number() OVER (PARTITION BY organization_id ORDER BY created_at, id) AS number FROM {TABLE}) n
        WHERE t.id = n.id
        """
    )
    op.execute(f"ALTER TABLE {TABLE} ENABLE TRIGGER trg_horse_journal_entries_final_immutable")
    op.execute(f"INSERT INTO record_counters (organization_id, series, next_number) SELECT organization_id, '{TABLE}', max(number) + 1 FROM {TABLE} GROUP BY organization_id")
    op.alter_column(TABLE, "number", nullable=False)
    op.create_unique_constraint(f"uq_{TABLE}_organization_number", TABLE, ["organization_id", "number"])
    op.execute(f"CREATE TRIGGER {TABLE}_record_number BEFORE INSERT OR UPDATE OF number ON {TABLE} FOR EACH ROW EXECUTE FUNCTION assign_record_number(1)")


def downgrade() -> None:
    op.execute(f"DROP TRIGGER {TABLE}_record_number ON {TABLE}")
    op.drop_constraint(f"uq_{TABLE}_organization_number", TABLE, type_="unique")
    op.drop_column(TABLE, "number")
    op.execute(f"DELETE FROM record_counters WHERE series = '{TABLE}'")
