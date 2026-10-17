"""scheduling: an appointment can be about several records (several horses of a stable booked for one long session)

The single (subject_type, subject_id) pair on `appointments` becomes rows of `appointment_subjects`, one per record the appointment
is about, so a stable can book one session for many horses. Existing pairs are copied. The downgrade keeps one subject per
appointment (the first by id), which is all the old shape could hold.

Revision ID: d7a9c1e3f5b6
Revises: c6f8b0d2e4a5
Create Date: 2026-10-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d7a9c1e3f5b6"
down_revision: Union[str, Sequence[str], None] = "c6f8b0d2e4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "appointment_subjects",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("appointment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_type", sa.String(64), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["organization_id", "appointment_id"], ["appointments.organization_id", "appointments.id"], ondelete="CASCADE", name="fk_appointment_subjects_appointment"
        ),
        sa.UniqueConstraint("appointment_id", "subject_type", "subject_id", name="uq_appointment_subjects_once"),
    )
    op.create_index("ix_appointment_subjects_organization_id", "appointment_subjects", ["organization_id"])
    op.create_index("ix_appointment_subjects_subject", "appointment_subjects", ["organization_id", "subject_type", "subject_id"])
    op.execute(
        """
        INSERT INTO appointment_subjects (organization_id, appointment_id, subject_type, subject_id)
        SELECT organization_id, id, subject_type, subject_id FROM appointments WHERE subject_type IS NOT NULL
        """
    )
    op.drop_index("ix_appointments_organization_subject", table_name="appointments")
    op.drop_constraint("ck_appointments_subject_pair", "appointments", type_="check")
    op.drop_column("appointments", "subject_type")
    op.drop_column("appointments", "subject_id")


def downgrade() -> None:
    op.add_column("appointments", sa.Column("subject_type", sa.String(64), nullable=True))
    op.add_column("appointments", sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.execute(
        """
        UPDATE appointments a SET subject_type = s.subject_type, subject_id = s.subject_id
        FROM (SELECT DISTINCT ON (appointment_id) appointment_id, subject_type, subject_id FROM appointment_subjects ORDER BY appointment_id, subject_id) s
        WHERE s.appointment_id = a.id
        """
    )
    op.create_check_constraint("ck_appointments_subject_pair", "appointments", "(subject_type IS NULL) = (subject_id IS NULL)")
    op.create_index("ix_appointments_organization_subject", "appointments", ["organization_id", "subject_type", "subject_id"])
    op.drop_table("appointment_subjects")
