"""create horses table

Revision ID: 4da669b016cc
Revises: d494542bdb90
Create Date: 2026-10-02 00:08:41.991359

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4da669b016cc'
down_revision: Union[str, Sequence[str], None] = 'd494542bdb90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Must exist first: the horses foreign keys below reference (organization_id, id).
    op.create_unique_constraint('uq_customers_organization_id_id', 'customers', ['organization_id', 'id'])
    op.create_table('horses',
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('owner_customer_id', sa.UUID(), nullable=False),
    sa.Column('stable_customer_id', sa.UUID(), nullable=True),
    sa.Column('birth_year', sa.SmallInteger(), nullable=True),
    sa.Column('sex', sa.String(length=16), nullable=True),
    sa.Column('breed', sa.String(length=100), nullable=True),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("sex IN ('mare', 'stallion', 'gelding')", name='ck_horses_sex'),
    sa.CheckConstraint('birth_year BETWEEN 1900 AND 2100', name='ck_horses_birth_year_range'),
    sa.ForeignKeyConstraint(['organization_id', 'owner_customer_id'], ['customers.organization_id', 'customers.id'], name='fk_horses_owner_same_organization', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'stable_customer_id'], ['customers.organization_id', 'customers.id'], name='fk_horses_stable_same_organization', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_horses_organization_id'), 'horses', ['organization_id'], unique=False)
    op.create_index('ix_horses_organization_owner', 'horses', ['organization_id', 'owner_customer_id'], unique=False)
    op.create_index('ix_horses_organization_stable', 'horses', ['organization_id', 'stable_customer_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('horses')
    # After the horses table is gone, nothing references the constraint any more.
    op.drop_constraint('uq_customers_organization_id_id', 'customers', type_='unique')
