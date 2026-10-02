"""create transactions and transaction lines

Revision ID: 0b25621ad645
Revises: 4da669b016cc
Create Date: 2026-10-02 00:24:12.560627

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0b25621ad645'
down_revision: Union[str, Sequence[str], None] = '4da669b016cc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Must exist first: transaction_lines references items (organization_id, id).
    op.create_unique_constraint('uq_items_organization_id_id', 'items', ['organization_id', 'id'])
    op.create_table('transactions',
    sa.Column('billing_customer_id', sa.UUID(), nullable=False),
    sa.Column('transaction_date', sa.Date(), nullable=False),
    sa.Column('status', sa.String(length=16), server_default=sa.text("'draft'"), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('draft', 'completed', 'cancelled')", name='ck_transactions_status'),
    sa.ForeignKeyConstraint(['organization_id', 'billing_customer_id'], ['customers.organization_id', 'customers.id'], name='fk_transactions_billing_customer_same_organization', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'id', name='uq_transactions_organization_id_id')
    )
    op.create_index('ix_transactions_organization_billing_customer', 'transactions', ['organization_id', 'billing_customer_id'], unique=False)
    op.create_index(op.f('ix_transactions_organization_id'), 'transactions', ['organization_id'], unique=False)
    op.create_index('ix_transactions_organization_status_date', 'transactions', ['organization_id', 'status', 'transaction_date'], unique=False)
    op.create_table('transaction_lines',
    sa.Column('transaction_id', sa.UUID(), nullable=False),
    sa.Column('item_id', sa.UUID(), nullable=True),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('description', sa.String(length=255), nullable=False),
    sa.Column('unit', sa.String(length=32), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=12, scale=3), nullable=False),
    sa.Column('unit_price_ex_vat', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('vat_rate', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('net_amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('vat_amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('gross_amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('gross_amount = net_amount + vat_amount', name='ck_transaction_lines_gross_amount'),
    sa.CheckConstraint('net_amount = round(quantity * unit_price_ex_vat, 2)', name='ck_transaction_lines_net_amount'),
    sa.CheckConstraint('quantity > 0', name='ck_transaction_lines_quantity_positive'),
    sa.CheckConstraint('unit_price_ex_vat >= 0', name='ck_transaction_lines_price_nonnegative'),
    sa.CheckConstraint('vat_amount = round(net_amount * vat_rate / 100, 2)', name='ck_transaction_lines_vat_amount'),
    sa.CheckConstraint('vat_rate >= 0 AND vat_rate <= 100', name='ck_transaction_lines_vat_rate_range'),
    sa.ForeignKeyConstraint(['organization_id', 'item_id'], ['items.organization_id', 'items.id'], name='fk_transaction_lines_item_same_organization', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'transaction_id'], ['transactions.organization_id', 'transactions.id'], name='fk_transaction_lines_transaction_same_organization', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_transaction_lines_organization_id'), 'transaction_lines', ['organization_id'], unique=False)
    op.create_index('ix_transaction_lines_organization_item', 'transaction_lines', ['organization_id', 'item_id'], unique=False)
    op.create_index('ix_transaction_lines_organization_transaction', 'transaction_lines', ['organization_id', 'transaction_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('transaction_lines')
    op.drop_table('transactions')
    # Nothing references the constraint any more.
    op.drop_constraint('uq_items_organization_id_id', 'items', type_='unique')
