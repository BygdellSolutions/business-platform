"""add business profile fields and the organization default currency

Revision ID: d52b8f1a7c34
Revises: c41a7e5d9b20
Create Date: 2026-10-03 14:00:00.000000

Additive and data-preserving: every new column is nullable and existing rows keep NULL.
In particular organizations.default_currency is NOT backfilled: no currency is assumed for an
existing organization, an owner or admin sets it explicitly.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd52b8f1a7c34'
down_revision: Union[str, Sequence[str], None] = 'c41a7e5d9b20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PROFILE_COLUMNS = [
    ('address_line1', 255),
    ('address_line2', 255),
    ('postal_code', 32),
    ('city', 128),
    ('country_code', 2),
    ('registration_number', 64),
    ('vat_number', 64),
]


def upgrade() -> None:
    """Upgrade schema."""
    for table in ('organizations', 'customers'):
        for name, length in PROFILE_COLUMNS:
            op.add_column(table, sa.Column(name, sa.String(length=length), nullable=True))
        op.create_check_constraint(f'ck_{table}_country_code_shape', table, "country_code IS NULL OR country_code ~ '^[A-Z]{2}$'")
    op.add_column('organizations', sa.Column('default_currency', sa.String(length=3), nullable=True))
    op.add_column('organizations', sa.Column('legal_name', sa.String(length=255), nullable=True))
    op.create_check_constraint('ck_organizations_default_currency_shape', 'organizations', "default_currency IS NULL OR default_currency ~ '^[A-Z]{3}$'")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('ck_organizations_default_currency_shape', 'organizations', type_='check')
    op.drop_column('organizations', 'legal_name')
    op.drop_column('organizations', 'default_currency')
    for table in ('customers', 'organizations'):
        op.drop_constraint(f'ck_{table}_country_code_shape', table, type_='check')
        for name, _ in reversed(PROFILE_COLUMNS):
            op.drop_column(table, name)
