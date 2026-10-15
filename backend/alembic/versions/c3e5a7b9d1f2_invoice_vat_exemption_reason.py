"""invoices: why no VAT is charged (the stated ground of a 0 % invoice)

Revision ID: c3e5a7b9d1f2
Revises: b2d4f6a8c0e1
Create Date: 2026-10-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3e5a7b9d1f2"
down_revision: Union[str, Sequence[str], None] = "b2d4f6a8c0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("vat_exemption_reason", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "vat_exemption_reason")
