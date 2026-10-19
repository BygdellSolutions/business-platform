"""receipt emails: a log of every attempt to email the receipt of an order paid at the counter

The same log as `invoice_emails`, for a receipt, which belongs to its order. The foreign key is composite (organization_id, transaction_id),
so the database refuses a log row for another organization's order. No document text and no copy of the PDF is stored.

Revision ID: c1e3a5b7d9f0
Revises: a7c9e1f3b5d6
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c1e3a5b7d9f0"
down_revision: Union[str, Sequence[str], None] = "a7c9e1f3b5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "receipt_emails",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("to_email", sa.String(254), nullable=False),
        sa.Column("reply_to", sa.String(254), nullable=True),
        sa.Column("sender_address", sa.String(254), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("note", sa.String(2000), nullable=True),
        sa.Column("pdf_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("error", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["organization_id", "transaction_id"], ["transactions.organization_id", "transactions.id"], ondelete="RESTRICT", name="fk_receipt_emails_transaction_same_organization"
        ),
        sa.CheckConstraint("status IN ('sent', 'queued', 'failed')", name="ck_receipt_emails_status"),
        sa.CheckConstraint("pdf_sha256 ~ '^[0-9a-f]{64}$'", name="ck_receipt_emails_hash_shape"),
    )
    op.create_index("ix_receipt_emails_organization_id", "receipt_emails", ["organization_id"])
    op.create_index("ix_receipt_emails_transaction", "receipt_emails", ["organization_id", "transaction_id", "created_at"])
    op.create_index("ix_receipt_emails_organization_created", "receipt_emails", ["organization_id", "created_at"])


def downgrade() -> None:
    op.drop_table("receipt_emails")
