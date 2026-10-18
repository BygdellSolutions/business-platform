"""support tickets: messages from the support page, kept before any email is tried

Platform-level (no organization_id): a person who cannot sign in writes here too. `reference` is the short random ticket number
the sender is told. `email_status` records what happened to the email to support, so a message is never lost when email is not
configured yet or the provider is down.

Revision ID: e5a7c9b1d3f4
Revises: d4f6a8c0e2b3
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5a7c9b1d3f4"
down_revision: Union[str, Sequence[str], None] = "d4f6a8c0e2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "support_tickets",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("reference", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("status", sa.String(10), server_default="open", nullable=False),
        sa.Column("name", sa.String(120), nullable=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("email_lower", sa.String(254), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source", sa.String(64), nullable=True),
        sa.Column("email_status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("email_error", sa.String(32), nullable=True),
        sa.Column("confirmation_status", sa.String(16), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference", name="uq_support_tickets_reference"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("status IN ('open', 'closed')", name="ck_support_tickets_status"),
        sa.CheckConstraint("email_status IN ('pending', 'sent', 'queued', 'not_configured', 'failed')", name="ck_support_tickets_email_status"),
        sa.CheckConstraint("length(btrim(message)) > 0 AND length(message) <= 4000", name="ck_support_tickets_message_length"),
    )
    op.create_index("ix_support_tickets_email_created", "support_tickets", ["email_lower", "created_at"])
    op.create_index("ix_support_tickets_status_created", "support_tickets", ["status", "created_at"])


def downgrade() -> None:
    op.drop_table("support_tickets")
