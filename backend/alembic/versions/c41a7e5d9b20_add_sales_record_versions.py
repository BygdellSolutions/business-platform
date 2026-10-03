"""add optimistic-concurrency versions to transactions and lines

Revision ID: c41a7e5d9b20
Revises: 2becbd93a5c1
Create Date: 2026-10-03 12:00:00.000000

Additive and data-preserving: every existing row starts at version 1.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c41a7e5d9b20'
down_revision: Union[str, Sequence[str], None] = '2becbd93a5c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('transactions', sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False))
    op.add_column('transactions', sa.Column('header_version', sa.Integer(), server_default=sa.text('1'), nullable=False))
    op.create_check_constraint('ck_transactions_versions_positive', 'transactions', 'version >= 1 AND header_version >= 1')
    op.add_column('transaction_lines', sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False))
    op.create_check_constraint('ck_transaction_lines_version_positive', 'transaction_lines', 'version >= 1')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('ck_transaction_lines_version_positive', 'transaction_lines', type_='check')
    op.drop_column('transaction_lines', 'version')
    op.drop_constraint('ck_transactions_versions_positive', 'transactions', type_='check')
    op.drop_column('transactions', 'header_version')
    op.drop_column('transactions', 'version')
