"""scheduling: appointments (a booking of time for a practitioner, optionally for a customer and a subject such as a horse)

Revision ID: b5e7a9c1d3f4
Revises: d0f2a4c6e8b9
Create Date: 2026-10-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b5e7a9c1d3f4"
down_revision: Union[str, Sequence[str], None] = "d0f2a4c6e8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "appointments"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("number", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), server_default="scheduled", nullable=False),
        sa.Column("practitioner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subject_type", sa.String(64), nullable=True),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["practitioner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["organization_id", "customer_id"], ["customers.organization_id", "customers.id"], ondelete="RESTRICT", name="fk_appointments_customer_same_organization"
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_appointments_organization_id_id"),
        sa.UniqueConstraint("organization_id", "number", name="uq_appointments_organization_number"),
        sa.CheckConstraint("ends_at > starts_at", name="ck_appointments_ends_after_start"),
        sa.CheckConstraint("ends_at - starts_at <= interval '7 days'", name="ck_appointments_at_most_a_week"),
        sa.CheckConstraint("status IN ('scheduled', 'cancelled')", name="ck_appointments_status"),
        sa.CheckConstraint("(subject_type IS NULL) = (subject_id IS NULL)", name="ck_appointments_subject_pair"),
        sa.CheckConstraint("version >= 1", name="ck_appointments_version_positive"),
    )
    op.create_index("ix_appointments_organization_id", TABLE, ["organization_id"])
    op.create_index("ix_appointments_organization_starts", TABLE, ["organization_id", "starts_at"])
    op.create_index("ix_appointments_organization_practitioner_starts", TABLE, ["organization_id", "practitioner_user_id", "starts_at"])
    op.create_index("ix_appointments_organization_subject", TABLE, ["organization_id", "subject_type", "subject_id"])
    op.execute(f"CREATE TRIGGER {TABLE}_record_number BEFORE INSERT OR UPDATE OF number ON {TABLE} FOR EACH ROW EXECUTE FUNCTION assign_record_number(1)")


def downgrade() -> None:
    op.execute(f"DROP TRIGGER {TABLE}_record_number ON {TABLE}")
    op.drop_table(TABLE)
    op.execute(f"DELETE FROM record_counters WHERE series = '{TABLE}'")
