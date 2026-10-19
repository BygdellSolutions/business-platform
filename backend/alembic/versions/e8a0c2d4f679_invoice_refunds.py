"""invoicing: refunds are payment rows with a negative amount

A refund (money paid back after a credit note) is stored in `invoice_payments` as a negative amount that reverses
nothing, and a reversal of a refund is positive. The sign rule moves to the service; the CHECK keeps amounts non-zero.
No rows change.

Revision ID: e8a0c2d4f679
Revises: d7f9b1c3e568
Create Date: 2026-10-10
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e8a0c2d4f679"
down_revision: Union[str, Sequence[str], None] = "d7f9b1c3e568"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_invoice_payments_amount_sign", "invoice_payments", type_="check")
    op.create_check_constraint("ck_invoice_payments_amount_sign", "invoice_payments", "amount <> 0")


def downgrade() -> None:
    # Refuses while refunds exist (they break the old rule): delete nothing to make it pass.
    op.drop_constraint("ck_invoice_payments_amount_sign", "invoice_payments", type_="check")
    op.create_check_constraint(
        "ck_invoice_payments_amount_sign",
        "invoice_payments",
        "(reverses_payment_id IS NULL AND amount > 0) OR (reverses_payment_id IS NOT NULL AND amount < 0)",
    )
