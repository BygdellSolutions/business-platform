"""service lines: a catalog service performed for a subject, by someone, at a time

Revision ID: f7b9d1e3a568
Revises: e5a7c9d1f246
Create Date: 2026-10-08 07:00:00.000000

Every existing line is a "standard" line (a catalog item or an ad-hoc line, as before); nothing is reclassified as
a service. Invoice lines gain a service snapshot, NULL for every existing line.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f7b9d1e3a568"
down_revision: Union[str, Sequence[str], None] = "e5a7c9d1f246"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SERVICE_FIELDS = (
    "(kind = 'service' AND item_id IS NOT NULL AND performed_at IS NOT NULL AND subject_type IS NOT NULL AND subject_id IS NOT NULL)"
    " OR (kind = 'standard' AND performed_at IS NULL AND performed_by IS NULL AND subject_type IS NULL AND subject_id IS NULL)"
)


def upgrade() -> None:
    op.add_column("transaction_lines", sa.Column("kind", sa.String(length=16), server_default=sa.text("'standard'"), nullable=False))
    op.add_column("transaction_lines", sa.Column("performed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("transaction_lines", sa.Column("performed_by", sa.UUID(), nullable=True))
    op.add_column("transaction_lines", sa.Column("subject_type", sa.String(length=64), nullable=True))
    op.add_column("transaction_lines", sa.Column("subject_id", sa.UUID(), nullable=True))
    op.add_column("transaction_lines", sa.Column("notes", sa.Text(), nullable=True))
    op.create_foreign_key("fk_transaction_lines_performed_by", "transaction_lines", "users", ["performed_by"], ["id"], ondelete="RESTRICT")
    op.create_check_constraint("ck_transaction_lines_kind", "transaction_lines", "kind IN ('standard', 'service')")
    op.create_check_constraint("ck_transaction_lines_service_fields", "transaction_lines", SERVICE_FIELDS)
    op.create_index("ix_transaction_lines_subject", "transaction_lines", ["organization_id", "subject_type", "subject_id"])

    op.add_column("invoice_lines", sa.Column("service", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_check_constraint("ck_invoice_lines_service_object", "invoice_lines", "service IS NULL OR jsonb_typeof(service) = 'object'")


def downgrade() -> None:
    op.drop_constraint("ck_invoice_lines_service_object", "invoice_lines", type_="check")
    op.drop_column("invoice_lines", "service")
    op.drop_index("ix_transaction_lines_subject", table_name="transaction_lines")
    op.drop_constraint("ck_transaction_lines_service_fields", "transaction_lines", type_="check")
    op.drop_constraint("ck_transaction_lines_kind", "transaction_lines", type_="check")
    op.drop_constraint("fk_transaction_lines_performed_by", "transaction_lines", type_="foreignkey")
    for column in ("notes", "subject_id", "subject_type", "performed_by", "performed_at", "kind"):
        op.drop_column("transaction_lines", column)
