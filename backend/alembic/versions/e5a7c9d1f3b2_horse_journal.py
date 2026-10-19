"""equine: the horse journal (one entry per visit: text and drawing marks, a draft until it is finalized)

Revision ID: e5a7c9d1f3b2
Revises: d4f6b8c0e2a3
Create Date: 2026-10-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5a7c9d1f3b2"
down_revision: Union[str, Sequence[str], None] = "d4f6b8c0e2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "horse_journal_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("horse_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("visit_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("practitioner_name", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), server_default="", nullable=False),
        sa.Column("follow_up_on", sa.Date(), nullable=True),
        sa.Column("marks", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("diagram_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("status", sa.String(16), server_default="draft", nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["finalized_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "horse_id"], ["horses.organization_id", "horses.id"], name="fk_horse_journal_entries_horse", ondelete="CASCADE"),
        sa.UniqueConstraint("organization_id", "id", name="uq_horse_journal_entries_organization_id_id"),
        sa.CheckConstraint("status IN ('draft', 'final')", name="ck_horse_journal_entries_status"),
        sa.CheckConstraint(
            "(status = 'final') = (finalized_at IS NOT NULL AND finalized_by IS NOT NULL)", name="ck_horse_journal_entries_final_stamped"
        ),
        sa.CheckConstraint("length(body) <= 20000", name="ck_horse_journal_entries_body_length"),
        sa.CheckConstraint("length(btrim(practitioner_name)) > 0", name="ck_horse_journal_entries_practitioner_not_blank"),
        sa.CheckConstraint("jsonb_typeof(marks) = 'array' AND jsonb_array_length(marks) <= 200", name="ck_horse_journal_entries_marks"),
        sa.CheckConstraint("version >= 1 AND diagram_version >= 1", name="ck_horse_journal_entries_versions_positive"),
    )
    op.create_index("ix_horse_journal_entries_organization_id", "horse_journal_entries", ["organization_id"])
    op.create_index("ix_horse_journal_entries_horse_visit", "horse_journal_entries", ["organization_id", "horse_id", sa.text("visit_at DESC"), "id"])


def downgrade() -> None:
    op.drop_index("ix_horse_journal_entries_horse_visit", table_name="horse_journal_entries")
    op.drop_index("ix_horse_journal_entries_organization_id", table_name="horse_journal_entries")
    op.drop_table("horse_journal_entries")
