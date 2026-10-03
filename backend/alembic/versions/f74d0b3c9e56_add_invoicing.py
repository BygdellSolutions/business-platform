"""add invoicing: invoices, sources, lines, VAT rows, counters and immutability triggers

Revision ID: f74d0b3c9e56
Revises: e63c9a2b8d45
Create Date: 2026-10-03 20:55:58.073741

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f74d0b3c9e56'
down_revision: Union[str, Sequence[str], None] = 'e63c9a2b8d45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # The key the invoice_transactions foreign key points at, so it must exist first.
    op.create_unique_constraint('uq_transactions_org_id_customer_currency', 'transactions', ['organization_id', 'id', 'billing_customer_id', 'currency'])
    op.create_table('invoice_counters',
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('series', sa.String(length=32), nullable=False),
    sa.Column('next_number', sa.BigInteger(), nullable=False),
    sa.CheckConstraint('next_number >= 1', name='ck_invoice_counters_next_positive'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('organization_id', 'series', name='pk_invoice_counters')
    )
    op.create_table('invoices',
    sa.Column('status', sa.String(length=16), server_default=sa.text("'draft'"), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('customer_id', sa.UUID(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('customer_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('issuer_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('customer_name', sa.String(length=255), nullable=False),
    sa.Column('invoice_date', sa.Date(), nullable=False),
    sa.Column('due_date', sa.Date(), nullable=True),
    sa.Column('description', sa.String(length=2000), nullable=True),
    sa.Column('series', sa.String(length=32), server_default=sa.text("'default'"), nullable=False),
    sa.Column('number', sa.BigInteger(), nullable=True),
    sa.Column('number_text', sa.String(length=64), nullable=True),
    sa.Column('issued_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('issued_by', sa.UUID(), nullable=True),
    sa.Column('net_amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('vat_amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('gross_amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name='ck_invoices_currency_shape'),
    sa.CheckConstraint("jsonb_typeof(customer_snapshot) = 'object' AND jsonb_typeof(issuer_snapshot) = 'object'", name='ck_invoices_snapshots_are_objects'),
    sa.CheckConstraint("series <> ''", name='ck_invoices_series_not_empty'),
    sa.CheckConstraint("status <> 'issued' OR (number IS NOT NULL AND number_text IS NOT NULL AND issued_at IS NOT NULL AND issued_by IS NOT NULL)", name='ck_invoices_issued_has_number'),
    sa.CheckConstraint("status = 'issued' OR (number IS NULL AND number_text IS NULL AND issued_at IS NULL AND issued_by IS NULL)", name='ck_invoices_draft_has_no_number'),
    sa.CheckConstraint("status IN ('draft', 'issued')", name='ck_invoices_status'),
    sa.CheckConstraint('due_date IS NULL OR due_date >= invoice_date', name='ck_invoices_due_after_invoice_date'),
    sa.CheckConstraint('net_amount >= 0 AND vat_amount >= 0 AND gross_amount = net_amount + vat_amount', name='ck_invoices_totals'),
    sa.CheckConstraint('number IS NULL OR number >= 1', name='ck_invoices_number_positive'),
    sa.CheckConstraint('version >= 1', name='ck_invoices_version_positive'),
    sa.ForeignKeyConstraint(['issued_by'], ['users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'customer_id'], ['customers.organization_id', 'customers.id'], name='fk_invoices_customer_same_organization', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'id', 'customer_id', 'currency', name='uq_invoices_org_id_customer_currency'),
    sa.UniqueConstraint('organization_id', 'id', name='uq_invoices_organization_id_id'),
    sa.UniqueConstraint('organization_id', 'series', 'number', name='uq_invoices_organization_series_number')
    )
    op.create_index('ix_invoices_organization_customer', 'invoices', ['organization_id', 'customer_id'], unique=False)
    op.create_index(op.f('ix_invoices_organization_id'), 'invoices', ['organization_id'], unique=False)
    op.create_index('ix_invoices_organization_status_date', 'invoices', ['organization_id', 'status', 'invoice_date'], unique=False)
    op.create_table('invoice_transactions',
    sa.Column('invoice_id', sa.UUID(), nullable=False),
    sa.Column('transaction_id', sa.UUID(), nullable=False),
    sa.Column('customer_id', sa.UUID(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('transaction_date', sa.Date(), nullable=False),
    sa.Column('source_version', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('fields', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("jsonb_typeof(fields) = 'array'", name='ck_invoice_transactions_fields_array'),
    sa.CheckConstraint('position >= 1 AND source_version >= 1', name='ck_invoice_transactions_positive'),
    sa.ForeignKeyConstraint(['organization_id', 'invoice_id', 'customer_id', 'currency'], ['invoices.organization_id', 'invoices.id', 'invoices.customer_id', 'invoices.currency'], name='fk_invoice_transactions_invoice_customer_currency', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id', 'transaction_id', 'customer_id', 'currency'], ['transactions.organization_id', 'transactions.id', 'transactions.billing_customer_id', 'transactions.currency'], name='fk_invoice_transactions_transaction_customer_currency', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'invoice_id', 'position', name='uq_invoice_transactions_position'),
    sa.UniqueConstraint('organization_id', 'invoice_id', 'transaction_id', name='uq_invoice_transactions_org_invoice_transaction'),
    sa.UniqueConstraint('organization_id', 'transaction_id', name='uq_invoice_transactions_source_once')
    )
    op.create_index(op.f('ix_invoice_transactions_organization_id'), 'invoice_transactions', ['organization_id'], unique=False)
    op.create_table('invoice_vat_rows',
    sa.Column('invoice_id', sa.UUID(), nullable=False),
    sa.Column('vat_rate', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('net_amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('vat_amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('net_amount >= 0 AND vat_amount >= 0', name='ck_invoice_vat_rows_nonnegative'),
    sa.CheckConstraint('vat_rate >= 0 AND vat_rate <= 100', name='ck_invoice_vat_rows_rate_range'),
    sa.ForeignKeyConstraint(['organization_id', 'invoice_id'], ['invoices.organization_id', 'invoices.id'], name='fk_invoice_vat_rows_invoice_same_organization', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'invoice_id', 'vat_rate', name='uq_invoice_vat_rows_rate')
    )
    op.create_index(op.f('ix_invoice_vat_rows_organization_id'), 'invoice_vat_rows', ['organization_id'], unique=False)
    op.create_table('invoice_lines',
    sa.Column('invoice_id', sa.UUID(), nullable=False),
    sa.Column('source_transaction_id', sa.UUID(), nullable=False),
    sa.Column('source_line_id', sa.UUID(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('description', sa.String(length=255), nullable=False),
    sa.Column('unit', sa.String(length=32), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=12, scale=3), nullable=False),
    sa.Column('unit_price_ex_vat', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('vat_rate', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('net_amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('vat_amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('gross_amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('fields', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("jsonb_typeof(fields) = 'array'", name='ck_invoice_lines_fields_array'),
    sa.CheckConstraint('gross_amount = net_amount + vat_amount', name='ck_invoice_lines_gross_amount'),
    sa.CheckConstraint('net_amount = round(quantity * unit_price_ex_vat, 2)', name='ck_invoice_lines_net_amount'),
    sa.CheckConstraint('position >= 1', name='ck_invoice_lines_position_positive'),
    sa.CheckConstraint('quantity > 0', name='ck_invoice_lines_quantity_positive'),
    sa.CheckConstraint('unit_price_ex_vat >= 0', name='ck_invoice_lines_price_nonnegative'),
    sa.CheckConstraint('vat_amount = round(net_amount * vat_rate / 100, 2)', name='ck_invoice_lines_vat_amount'),
    sa.CheckConstraint('vat_rate >= 0 AND vat_rate <= 100', name='ck_invoice_lines_vat_rate_range'),
    sa.ForeignKeyConstraint(['organization_id', 'invoice_id', 'source_transaction_id'], ['invoice_transactions.organization_id', 'invoice_transactions.invoice_id', 'invoice_transactions.transaction_id'], name='fk_invoice_lines_invoice_transaction', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id', 'source_line_id', 'source_transaction_id'], ['transaction_lines.organization_id', 'transaction_lines.id', 'transaction_lines.transaction_id'], name='fk_invoice_lines_source_line', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'invoice_id', 'position', name='uq_invoice_lines_position'),
    sa.UniqueConstraint('organization_id', 'source_line_id', name='uq_invoice_lines_source_line_once')
    )
    op.create_index(op.f('ix_invoice_lines_organization_id'), 'invoice_lines', ['organization_id'], unique=False)

    # --- immutability: invoice-local triggers (they know only the invoicing tables) ------------------
    # An issued invoice and its documents never change; a draft may change only what the API
    # supports (header data; snapshot content re-taken at issuance). Nothing here refers to Sales.
    op.execute(
        """
        CREATE FUNCTION invoices_immutability() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.status = 'issued' THEN
                    RAISE EXCEPTION 'an issued invoice cannot be deleted' USING ERRCODE = 'check_violation';
                END IF;
                RETURN OLD;
            END IF;
            IF OLD.status = 'issued' THEN
                RAISE EXCEPTION 'an issued invoice cannot be changed' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.id <> OLD.id OR NEW.organization_id <> OLD.organization_id
               OR NEW.customer_id <> OLD.customer_id OR NEW.currency <> OLD.currency
               OR NEW.series <> OLD.series
               OR NEW.net_amount <> OLD.net_amount OR NEW.vat_amount <> OLD.vat_amount
               OR NEW.gross_amount <> OLD.gross_amount THEN
                RAISE EXCEPTION 'the customer, currency and totals of an invoice cannot be changed'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_invoices_immutability
        BEFORE UPDATE OR DELETE ON invoices
        FOR EACH ROW EXECUTE FUNCTION invoices_immutability()
        """
    )
    op.execute(
        """
        CREATE FUNCTION invoice_children_immutability() RETURNS trigger AS $$
        DECLARE
            invoice uuid;
            org uuid;
            current_status text;
            mutable text := COALESCE(TG_ARGV[0], '');
        BEGIN
            IF TG_OP = 'DELETE' THEN
                invoice := OLD.invoice_id; org := OLD.organization_id;
            ELSE
                invoice := NEW.invoice_id; org := NEW.organization_id;
            END IF;
            -- During the deletion of a draft invoice the parent row is already gone: nothing to protect.
            SELECT status INTO current_status FROM invoices WHERE organization_id = org AND id = invoice;
            IF current_status = 'issued' THEN
                RAISE EXCEPTION 'the documents of an issued invoice cannot be changed'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF TG_OP = 'UPDATE' THEN
                IF NEW.invoice_id <> OLD.invoice_id THEN
                    RAISE EXCEPTION 'a document row cannot move to another invoice' USING ERRCODE = 'check_violation';
                END IF;
                -- While a draft, only the snapshot content named by the trigger argument may change.
                IF (to_jsonb(NEW) - mutable - 'updated_at') IS DISTINCT FROM (to_jsonb(OLD) - mutable - 'updated_at') THEN
                    RAISE EXCEPTION 'only snapshot content of a draft invoice may be changed'
                        USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table, mutable in (("invoice_transactions", "fields"), ("invoice_lines", "fields"), ("invoice_vat_rows", "")):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_immutability
            BEFORE INSERT OR UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION invoice_children_immutability('{mutable}')
            """
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table in ("invoice_vat_rows", "invoice_lines", "invoice_transactions"):
        op.execute(f"DROP TRIGGER trg_{table}_immutability ON {table}")
    op.execute("DROP FUNCTION invoice_children_immutability()")
    op.execute("DROP TRIGGER trg_invoices_immutability ON invoices")
    op.execute("DROP FUNCTION invoices_immutability()")
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f('ix_invoice_lines_organization_id'), table_name='invoice_lines')
    op.drop_table('invoice_lines')
    op.drop_index(op.f('ix_invoice_vat_rows_organization_id'), table_name='invoice_vat_rows')
    op.drop_table('invoice_vat_rows')
    op.drop_index(op.f('ix_invoice_transactions_organization_id'), table_name='invoice_transactions')
    op.drop_table('invoice_transactions')
    op.drop_index('ix_invoices_organization_status_date', table_name='invoices')
    op.drop_index(op.f('ix_invoices_organization_id'), table_name='invoices')
    op.drop_index('ix_invoices_organization_customer', table_name='invoices')
    op.drop_table('invoices')
    op.drop_table('invoice_counters')
    op.drop_constraint('uq_transactions_org_id_customer_currency', 'transactions', type_='unique')
    # ### end Alembic commands ###
