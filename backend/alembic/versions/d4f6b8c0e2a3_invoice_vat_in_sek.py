"""invoices: the VAT amount in SEK of an invoice in another currency (the exchange rate used, and the result frozen at issue)

Revision ID: d4f6b8c0e2a3
Revises: c3e5a7b9d1f2
Create Date: 2026-10-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4f6b8c0e2a3"
down_revision: Union[str, Sequence[str], None] = "c3e5a7b9d1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("vat_exchange_rate", sa.Numeric(12, 6), nullable=True))
    op.add_column("invoices", sa.Column("vat_amount_sek", sa.Numeric(18, 2), nullable=True))
    op.create_check_constraint("ck_invoices_vat_exchange_rate_positive", "invoices", "vat_exchange_rate IS NULL OR vat_exchange_rate > 0")


def downgrade() -> None:
    op.drop_constraint("ck_invoices_vat_exchange_rate_positive", "invoices", type_="check")
    op.drop_column("invoices", "vat_amount_sek")
    op.drop_column("invoices", "vat_exchange_rate")
