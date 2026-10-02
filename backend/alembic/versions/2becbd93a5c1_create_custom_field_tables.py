"""create custom field tables

Revision ID: 2becbd93a5c1
Revises: 0b25621ad645
Create Date: 2026-10-03 00:16:36.973970

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2becbd93a5c1'
down_revision: Union[str, Sequence[str], None] = '0b25621ad645'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('custom_field_definitions',
    sa.Column('entity_type', sa.String(length=64), nullable=False),
    sa.Column('key', sa.String(length=40), nullable=False),
    sa.Column('label', sa.String(length=100), nullable=False),
    sa.Column('field_type', sa.String(length=16), nullable=False),
    sa.Column('required', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('position', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('show_in_form', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('show_in_table', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('show_on_invoice', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('reference_source', sa.String(length=64), nullable=True),
    sa.Column('depends_on_definition_id', sa.UUID(), nullable=True),
    sa.Column('depends_on_filter', sa.String(length=64), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(field_type = 'reference') = (reference_source IS NOT NULL)", name='ck_custom_field_definitions_reference_source'),
    sa.CheckConstraint("depends_on_definition_id IS NULL OR field_type = 'reference'", name='ck_custom_field_definitions_dependency_only_references'),
    sa.CheckConstraint("field_type IN ('text', 'number', 'date', 'boolean', 'select', 'reference')", name='ck_custom_field_definitions_type'),
    sa.CheckConstraint("key ~ '^[a-z][a-z0-9_]{0,39}$'", name='ck_custom_field_definitions_key'),
    sa.CheckConstraint('(depends_on_definition_id IS NULL) = (depends_on_filter IS NULL)', name='ck_custom_field_definitions_dependency_pair'),
    sa.ForeignKeyConstraint(['organization_id', 'depends_on_definition_id'], ['custom_field_definitions.organization_id', 'custom_field_definitions.id'], name='fk_custom_field_definitions_depends_on_same_organization', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'entity_type', 'key', name='uq_custom_field_definitions_entity_key'),
    sa.UniqueConstraint('organization_id', 'id', 'entity_type', 'field_type', name='uq_custom_field_definitions_id_entity_type'),
    sa.UniqueConstraint('organization_id', 'id', 'field_type', name='uq_custom_field_definitions_id_type'),
    sa.UniqueConstraint('organization_id', 'id', name='uq_custom_field_definitions_org_id')
    )
    op.create_index(op.f('ix_custom_field_definitions_organization_id'), 'custom_field_definitions', ['organization_id'], unique=False)
    op.create_table('custom_field_options',
    sa.Column('definition_id', sa.UUID(), nullable=False),
    sa.Column('field_type', sa.String(length=16), server_default=sa.text("'select'"), nullable=False),
    sa.Column('label', sa.String(length=100), nullable=False),
    sa.Column('position', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("field_type = 'select'", name='ck_custom_field_options_select_only'),
    sa.ForeignKeyConstraint(['organization_id', 'definition_id', 'field_type'], ['custom_field_definitions.organization_id', 'custom_field_definitions.id', 'custom_field_definitions.field_type'], name='fk_custom_field_options_definition_same_organization', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'definition_id', 'id', name='uq_custom_field_options_definition_id')
    )
    op.create_index('ix_custom_field_options_organization_definition', 'custom_field_options', ['organization_id', 'definition_id'], unique=False)
    op.create_index(op.f('ix_custom_field_options_organization_id'), 'custom_field_options', ['organization_id'], unique=False)
    op.create_table('custom_field_values',
    sa.Column('definition_id', sa.UUID(), nullable=False),
    sa.Column('entity_type', sa.String(length=64), nullable=False),
    sa.Column('field_type', sa.String(length=16), nullable=False),
    sa.Column('entity_id', sa.UUID(), nullable=False),
    sa.Column('value_text', sa.String(length=2000), nullable=True),
    sa.Column('value_number', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('value_date', sa.Date(), nullable=True),
    sa.Column('value_boolean', sa.Boolean(), nullable=True),
    sa.Column('value_option_id', sa.UUID(), nullable=True),
    sa.Column('value_reference_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("num_nonnulls(value_text, value_number, value_date, value_boolean, value_option_id, value_reference_id) = 1 AND coalesce(CASE field_type WHEN 'text' THEN value_text IS NOT NULL WHEN 'number' THEN value_number IS NOT NULL WHEN 'date' THEN value_date IS NOT NULL WHEN 'boolean' THEN value_boolean IS NOT NULL WHEN 'select' THEN value_option_id IS NOT NULL WHEN 'reference' THEN value_reference_id IS NOT NULL END, false)", name='ck_custom_field_values_typed_value'),
    sa.ForeignKeyConstraint(['organization_id', 'definition_id', 'entity_type', 'field_type'], ['custom_field_definitions.organization_id', 'custom_field_definitions.id', 'custom_field_definitions.entity_type', 'custom_field_definitions.field_type'], name='fk_custom_field_values_definition_same_organization', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'definition_id', 'value_option_id'], ['custom_field_options.organization_id', 'custom_field_options.definition_id', 'custom_field_options.id'], name='fk_custom_field_values_option_of_definition', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'definition_id', 'entity_id', name='uq_custom_field_values_field_entity')
    )
    op.create_index('ix_custom_field_values_organization_entity', 'custom_field_values', ['organization_id', 'entity_type', 'entity_id'], unique=False)
    op.create_index(op.f('ix_custom_field_values_organization_id'), 'custom_field_values', ['organization_id'], unique=False)
    op.create_index('ix_custom_field_values_organization_option', 'custom_field_values', ['organization_id', 'value_option_id'], unique=False, postgresql_where=sa.text('value_option_id IS NOT NULL'))
    op.create_index('ix_custom_field_values_organization_reference', 'custom_field_values', ['organization_id', 'value_reference_id'], unique=False, postgresql_where=sa.text('value_reference_id IS NOT NULL'))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_custom_field_values_organization_reference', table_name='custom_field_values', postgresql_where=sa.text('value_reference_id IS NOT NULL'))
    op.drop_index('ix_custom_field_values_organization_option', table_name='custom_field_values', postgresql_where=sa.text('value_option_id IS NOT NULL'))
    op.drop_index(op.f('ix_custom_field_values_organization_id'), table_name='custom_field_values')
    op.drop_index('ix_custom_field_values_organization_entity', table_name='custom_field_values')
    op.drop_table('custom_field_values')
    op.drop_index(op.f('ix_custom_field_options_organization_id'), table_name='custom_field_options')
    op.drop_index('ix_custom_field_options_organization_definition', table_name='custom_field_options')
    op.drop_table('custom_field_options')
    op.drop_index(op.f('ix_custom_field_definitions_organization_id'), table_name='custom_field_definitions')
    op.drop_table('custom_field_definitions')
