"""scheduling: services on an appointment and the draft order made from it; where an order line came from

* `appointment_items`: the catalog services an appointment is for (a long session for many horses is billed as these services, once for
  each horse).
* `appointments.order_id`: the draft order made from the appointment (no foreign key: Scheduling never knows what an order is; Sales
  answers for it through the registry, as for the journal's order link).
* `transaction_lines.source_type/source_id`: where a line came from (an appointment), so that updating the order from the appointment
  touches only those lines and leaves what a person added by hand.

Revision ID: a9c1e3f5b7d8
Revises: f3c5e7a9b1d2
Create Date: 2026-10-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a9c1e3f5b7d8"
down_revision: Union[str, Sequence[str], None] = "f3c5e7a9b1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "appointment_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("appointment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["organization_id", "appointment_id"], ["appointments.organization_id", "appointments.id"], ondelete="CASCADE", name="fk_appointment_items_appointment"
        ),
        sa.ForeignKeyConstraint(["organization_id", "item_id"], ["items.organization_id", "items.id"], ondelete="RESTRICT", name="fk_appointment_items_item_same_organization"),
        sa.UniqueConstraint("appointment_id", "item_id", name="uq_appointment_items_once"),
    )
    op.create_index("ix_appointment_items_organization_id", "appointment_items", ["organization_id"])
    op.add_column("appointments", sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("transaction_lines", sa.Column("source_type", sa.String(32), nullable=True))
    op.add_column("transaction_lines", sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_check_constraint("ck_transaction_lines_source_pair", "transaction_lines", "(source_type IS NULL) = (source_id IS NULL)")
    op.create_index("ix_transaction_lines_source", "transaction_lines", ["organization_id", "source_type", "source_id"])


def downgrade() -> None:
    op.drop_index("ix_transaction_lines_source", table_name="transaction_lines")
    op.drop_constraint("ck_transaction_lines_source_pair", "transaction_lines", type_="check")
    op.drop_column("transaction_lines", "source_id")
    op.drop_column("transaction_lines", "source_type")
    op.drop_column("appointments", "order_id")
    op.drop_table("appointment_items")
