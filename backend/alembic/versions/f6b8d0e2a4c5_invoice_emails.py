"""invoice emails: a log of every attempt to email an issued invoice to a customer

One row per attempt (kept also when it failed): to whom, from which sender address, with what subject, who pressed send, what the
mail provider said, and the SHA-256 of the exact PDF that was attached. The foreign key is composite (organization_id, invoice_id),
so the database refuses a log row for another organization's invoice. No document text and no copy of the PDF is stored here.

Revision ID: f6b8d0e2a4c5
Revises: e5a7c9b1d3f4
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f6b8d0e2a4c5"
down_revision: Union[str, Sequence[str], None] = "e5a7c9b1d3f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invoice_emails",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("to_email", sa.String(254), nullable=False),
        sa.Column("reply_to", sa.String(254), nullable=True),
        sa.Column("sender_address", sa.String(254), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("note", sa.String(1000), nullable=True),
        sa.Column("pdf_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("error", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["organization_id", "invoice_id"], ["invoices.organization_id", "invoices.id"], ondelete="RESTRICT", name="fk_invoice_emails_invoice_same_organization"
        ),
        sa.CheckConstraint("status IN ('sent', 'queued', 'failed')", name="ck_invoice_emails_status"),
        sa.CheckConstraint("pdf_sha256 ~ '^[0-9a-f]{64}$'", name="ck_invoice_emails_hash_shape"),
    )
    op.create_index("ix_invoice_emails_organization_id", "invoice_emails", ["organization_id"])
    op.create_index("ix_invoice_emails_invoice", "invoice_emails", ["organization_id", "invoice_id", "created_at"])
    op.create_index("ix_invoice_emails_organization_created", "invoice_emails", ["organization_id", "created_at"])


def downgrade() -> None:
    op.drop_table("invoice_emails")
