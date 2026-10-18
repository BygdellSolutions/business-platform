"""invoice email templates: the subject and the message an organization usually sends with an invoice

Two optional text columns on the organization. They hold pre-made text with placeholders ({customer}, {invoice_number}, ...) that
pre-fills the send form; they are not part of any stored invoice, so changing them never changes an invoice that was already issued
or an email that was already sent.

Revision ID: a7c9e1f3b5d6
Revises: f6b8d0e2a4c5
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7c9e1f3b5d6"
down_revision: Union[str, Sequence[str], None] = "f6b8d0e2a4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("invoice_email_subject", sa.String(200), nullable=True))
    op.add_column("organizations", sa.Column("invoice_email_message", sa.String(2000), nullable=True))
    # The message a person sends can now be as long as a template (2000), so the log keeps all of it.
    op.alter_column("invoice_emails", "note", type_=sa.String(2000), existing_type=sa.String(1000), existing_nullable=True)


def downgrade() -> None:
    op.execute("UPDATE invoice_emails SET note = left(note, 1000) WHERE length(note) > 1000")
    op.alter_column("invoice_emails", "note", type_=sa.String(1000), existing_type=sa.String(2000), existing_nullable=True)
    op.drop_column("organizations", "invoice_email_message")
    op.drop_column("organizations", "invoice_email_subject")
