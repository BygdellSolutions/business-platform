"""invoicing: payments recorded by hand

Additive: `invoice_payments` (amount, payment date, method, reference, note, who and when; a reversal names the
payment it cancels and carries the negative amount; each payment can be reversed once). Append-only: a trigger refuses
UPDATE and DELETE except while the organization itself is being deleted.

Revision ID: f3b5d7e9a124
Revises: e2a4c6d8f013
Create Date: 2026-10-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f3b5d7e9a124"
down_revision: Union[str, Sequence[str], None] = "e2a4c6d8f013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invoice_payments",
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("reference", sa.String(length=255), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("reverses_payment_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(reverses_payment_id IS NULL AND amount > 0) OR (reverses_payment_id IS NOT NULL AND amount < 0)", name="ck_invoice_payments_amount_sign"
        ),
        sa.CheckConstraint(
            "method IN ('bankgiro', 'plusgiro', 'bank_transfer', 'swish', 'card', 'cash', 'other')", name="ck_invoice_payments_method"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "invoice_id"], ["invoices.organization_id", "invoices.id"], name="fk_invoice_payments_invoice", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["organization_id", "reverses_payment_id"], ["invoice_payments.organization_id", "invoice_payments.id"], name="fk_invoice_payments_reverses", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_invoice_payments_organization_id_id"),
    )
    op.create_index(op.f("ix_invoice_payments_organization_id"), "invoice_payments", ["organization_id"])
    op.create_index("ix_invoice_payments_invoice", "invoice_payments", ["organization_id", "invoice_id"])
    op.create_index(
        "uq_invoice_payments_reversed_once", "invoice_payments", ["organization_id", "reverses_payment_id"], unique=True,
        postgresql_where=sa.text("reverses_payment_id IS NOT NULL"),
    )
    op.execute(
        """
        CREATE FUNCTION invoice_payments_append_only() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               AND current_setting('app.deleting_organization', true) = OLD.organization_id::text THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'invoice payments are append-only' USING ERRCODE = 'check_violation';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_invoice_payments_append_only
        BEFORE UPDATE OR DELETE ON invoice_payments
        FOR EACH ROW EXECUTE FUNCTION invoice_payments_append_only()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_invoice_payments_append_only ON invoice_payments")
    op.execute("DROP FUNCTION invoice_payments_append_only()")
    op.drop_index("uq_invoice_payments_reversed_once", table_name="invoice_payments")
    op.drop_index("ix_invoice_payments_invoice", table_name="invoice_payments")
    op.drop_index(op.f("ix_invoice_payments_organization_id"), table_name="invoice_payments")
    op.drop_table("invoice_payments")
