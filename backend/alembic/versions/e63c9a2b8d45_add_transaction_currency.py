"""add transaction currency, its immutability trigger and the transaction-line reference key

Revision ID: e63c9a2b8d45
Revises: d52b8f1a7c34
Create Date: 2026-10-03 14:10:00.000000

transactions.currency is nullable and NOT backfilled. Existing transactions keep NULL ("no
currency recorded"): labelling them with an assumed currency would silently reinterpret
financial data. They get a currency only through the explicit owner/admin action in the API.

Once a transaction has a currency it never changes: the trigger refuses any change from a
non-NULL value (NULL -> value is the one allowed transition).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e63c9a2b8d45'
down_revision: Union[str, Sequence[str], None] = 'd52b8f1a7c34'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('transactions', sa.Column('currency', sa.String(length=3), nullable=True))
    op.create_check_constraint('ck_transactions_currency_shape', 'transactions', "currency IS NULL OR currency ~ '^[A-Z]{3}$'")
    op.create_unique_constraint('uq_transaction_lines_org_id_transaction', 'transaction_lines', ['organization_id', 'id', 'transaction_id'])
    op.execute(
        """
        CREATE FUNCTION transactions_currency_is_immutable() RETURNS trigger AS $$
        BEGIN
            IF OLD.currency IS NOT NULL AND NEW.currency IS DISTINCT FROM OLD.currency THEN
                RAISE EXCEPTION 'transactions.currency cannot be changed once set'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_transactions_currency_immutable
        BEFORE UPDATE OF currency ON transactions
        FOR EACH ROW EXECUTE FUNCTION transactions_currency_is_immutable()
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute('DROP TRIGGER trg_transactions_currency_immutable ON transactions')
    op.execute('DROP FUNCTION transactions_currency_is_immutable()')
    op.drop_constraint('uq_transaction_lines_org_id_transaction', 'transaction_lines', type_='unique')
    op.drop_constraint('ck_transactions_currency_shape', 'transactions', type_='check')
    op.drop_column('transactions', 'currency')
