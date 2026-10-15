"""the horse journal: a finalized entry is frozen by the database, and corrections are dated additions that are never changed

A finalized entry refuses every UPDATE and DELETE (a DELETE only while its whole organization is being deleted, the same
transaction-local escape the invoices use). `horse_journal_corrections` holds later additions to a finalized entry: written once
(append-only, same rule), each with its author and time.

Revision ID: f6b8d0e2a4c3
Revises: e5a7c9d1f3b2
Create Date: 2026-10-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f6b8d0e2a4c3"
down_revision: Union[str, Sequence[str], None] = "e5a7c9d1f3b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DELETING = "current_setting('app.deleting_organization', true) = OLD.organization_id::text"


def upgrade() -> None:
    op.create_table(
        "horse_journal_corrections",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("entry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["organization_id", "entry_id"], ["horse_journal_entries.organization_id", "horse_journal_entries.id"],
            name="fk_horse_journal_corrections_entry", ondelete="CASCADE",
        ),
        sa.CheckConstraint("length(btrim(body)) > 0 AND length(body) <= 5000", name="ck_horse_journal_corrections_body"),
    )
    op.create_index("ix_horse_journal_corrections_organization_id", "horse_journal_corrections", ["organization_id"])
    op.create_index("ix_horse_journal_corrections_entry", "horse_journal_corrections", ["organization_id", "entry_id", "created_at"])
    op.execute(
        f"""
        CREATE FUNCTION horse_journal_entries_final_immutable() RETURNS trigger AS $$
        BEGIN
            IF OLD.status = 'final' THEN
                IF TG_OP = 'DELETE' AND {DELETING} THEN RETURN OLD; END IF;
                RAISE EXCEPTION 'a finalized journal entry cannot be changed or deleted' USING ERRCODE = 'check_violation';
            END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER trg_horse_journal_entries_final_immutable BEFORE UPDATE OR DELETE ON horse_journal_entries "
        "FOR EACH ROW EXECUTE FUNCTION horse_journal_entries_final_immutable()"
    )
    op.execute(
        f"""
        CREATE FUNCTION horse_journal_corrections_append_only() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND {DELETING} THEN RETURN OLD; END IF;
            RAISE EXCEPTION 'a journal correction cannot be changed or deleted' USING ERRCODE = 'check_violation';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER trg_horse_journal_corrections_append_only BEFORE UPDATE OR DELETE ON horse_journal_corrections "
        "FOR EACH ROW EXECUTE FUNCTION horse_journal_corrections_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_horse_journal_corrections_append_only ON horse_journal_corrections")
    op.execute("DROP FUNCTION horse_journal_corrections_append_only()")
    op.execute("DROP TRIGGER trg_horse_journal_entries_final_immutable ON horse_journal_entries")
    op.execute("DROP FUNCTION horse_journal_entries_final_immutable()")
    op.drop_index("ix_horse_journal_corrections_entry", table_name="horse_journal_corrections")
    op.drop_index("ix_horse_journal_corrections_organization_id", table_name="horse_journal_corrections")
    op.drop_table("horse_journal_corrections")
