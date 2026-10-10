"""counter sales: an order can be paid at the counter (with a receipt) instead of invoiced; a walk-in customer

`transactions` gains `paid_at`, `payment_method` (swish, card, cash), `receipt_number` (its own series per
organization, `record_counters` series "receipts", from 1001) and `receipt_number_text`. They are set together, only
on a completed order, so a paid order can never be reopened or cancelled. Receipt numbers are unique per organization.

`customers.walk_in` marks the organization's one "Walk-in customer" (at most one per organization), used for a counter
sale without a named customer; it is never invoiced.

Additive: existing rows are unpaid and not walk-in.

Revision ID: a7c9e1f3b457
Revises: f6b8d0e2a346
Create Date: 2026-10-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7c9e1f3b457"
down_revision: Union[str, Sequence[str], None] = "f6b8d0e2a346"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("transactions", sa.Column("payment_method", sa.String(length=16), nullable=True))
    op.add_column("transactions", sa.Column("receipt_number", sa.BigInteger(), nullable=True))
    op.add_column("transactions", sa.Column("receipt_number_text", sa.String(length=32), nullable=True))
    op.create_check_constraint(
        "ck_transactions_paid_fields_together",
        "transactions",
        "(paid_at IS NULL) = (payment_method IS NULL) AND (paid_at IS NULL) = (receipt_number IS NULL)"
        " AND (receipt_number IS NULL) = (receipt_number_text IS NULL)",
    )
    op.create_check_constraint("ck_transactions_paid_is_completed", "transactions", "paid_at IS NULL OR status = 'completed'")
    op.create_check_constraint(
        "ck_transactions_payment_method", "transactions", "payment_method IS NULL OR payment_method IN ('swish', 'card', 'cash')"
    )
    op.create_unique_constraint("uq_transactions_organization_receipt_number", "transactions", ["organization_id", "receipt_number"])

    op.add_column("customers", sa.Column("walk_in", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.create_index("uq_customers_one_walk_in", "customers", ["organization_id"], unique=True, postgresql_where=sa.text("walk_in"))


def downgrade() -> None:
    op.drop_index("uq_customers_one_walk_in", table_name="customers")
    op.drop_column("customers", "walk_in")
    op.drop_constraint("uq_transactions_organization_receipt_number", "transactions", type_="unique")
    for name in ("ck_transactions_payment_method", "ck_transactions_paid_is_completed", "ck_transactions_paid_fields_together"):
        op.drop_constraint(name, "transactions", type_="check")
    for column in ("receipt_number_text", "receipt_number", "payment_method", "paid_at"):
        op.drop_column("transactions", column)
