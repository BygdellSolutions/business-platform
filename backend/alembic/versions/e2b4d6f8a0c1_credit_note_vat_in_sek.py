"""credit notes: the VAT amount in SEK of a credit note on an invoice in another currency (the invoice's exchange rate, the result frozen)

A credit note of a foreign-currency invoice credits VAT, and that VAT is stated in SEK too. It uses the exchange rate of the invoice it
credits (the credit undoes part of that invoice), and the result is calculated once when the credit note is made. Credit notes are
append-only, so both columns are written at the insert.

Revision ID: e2b4d6f8a0c1
Revises: d7a9c1e3f5b6
Create Date: 2026-10-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2b4d6f8a0c1"
down_revision: Union[str, Sequence[str], None] = "d7a9c1e3f5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("credit_notes", sa.Column("vat_exchange_rate", sa.Numeric(12, 6), nullable=True))
    op.add_column("credit_notes", sa.Column("vat_amount_sek", sa.Numeric(18, 2), nullable=True))
    op.create_check_constraint("ck_credit_notes_vat_exchange_rate_positive", "credit_notes", "vat_exchange_rate IS NULL OR vat_exchange_rate > 0")


def downgrade() -> None:
    op.drop_constraint("ck_credit_notes_vat_exchange_rate_positive", "credit_notes", type_="check")
    op.drop_column("credit_notes", "vat_amount_sek")
    op.drop_column("credit_notes", "vat_exchange_rate")
