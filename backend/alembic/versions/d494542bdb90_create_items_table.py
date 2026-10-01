"""create items table

Revision ID: d494542bdb90
Revises: 8a73bf583823
Create Date: 2026-10-01 23:53:32.987826

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd494542bdb90'
down_revision: Union[str, Sequence[str], None] = '8a73bf583823'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('items',
    sa.Column('type', sa.String(length=16), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('unit', sa.String(length=32), nullable=False),
    sa.Column('price_ex_vat', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('vat_rate', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("type IN ('service', 'product')", name='ck_items_type'),
    sa.CheckConstraint('price_ex_vat >= 0', name='ck_items_price_ex_vat_nonnegative'),
    sa.CheckConstraint('vat_rate >= 0 AND vat_rate <= 100', name='ck_items_vat_rate_range'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_items_organization_id'), 'items', ['organization_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_items_organization_id'), table_name='items')
    op.drop_table('items')
