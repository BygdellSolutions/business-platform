"""invoicing: credit notes (kreditfakturor)

Additive: `credit_notes` (numbered from the invoice's series; a reason; copies of the customer and seller; positive
totals), `credit_note_lines` (a quantity of one invoice line at its own price and VAT; whether the goods went back into
stock) and `credit_note_vat_rows`. A unique (organization_id, id) on `invoice_lines` is the target of the lines'
composite foreign key. All three tables are append-only: a trigger refuses UPDATE and DELETE except while the
organization itself is being deleted.

Revision ID: d7f9b1c3e568
Revises: c6e8a0b2d457
Create Date: 2026-10-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d7f9b1c3e568"
down_revision: Union[str, Sequence[str], None] = "c6e8a0b2d457"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("credit_notes", "credit_note_lines", "credit_note_vat_rows")


def _tenant_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.create_unique_constraint("uq_invoice_lines_organization_id_id", "invoice_lines", ["organization_id", "id"])

    op.create_table(
        "credit_notes",
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column("series", sa.String(length=32), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("number_text", sa.String(length=64), nullable=False),
        sa.Column("credit_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("customer_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("issuer_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("net_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("vat_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("gross_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issued_by", sa.UUID(), nullable=False),
        *_tenant_columns(),
        sa.CheckConstraint("number >= 1", name="ck_credit_notes_number_positive"),
        sa.CheckConstraint("length(btrim(reason)) > 0", name="ck_credit_notes_reason"),
        sa.CheckConstraint("net_amount > 0 AND vat_amount >= 0 AND gross_amount = net_amount + vat_amount", name="ck_credit_notes_totals"),
        sa.CheckConstraint("jsonb_typeof(customer_snapshot) = 'object' AND jsonb_typeof(issuer_snapshot) = 'object'", name="ck_credit_notes_snapshots"),
        sa.ForeignKeyConstraint(["issued_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "invoice_id"], ["invoices.organization_id", "invoices.id"], name="fk_credit_notes_invoice", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_credit_notes_organization_id_id"),
        sa.UniqueConstraint("organization_id", "series", "number", name="uq_credit_notes_organization_series_number"),
    )
    op.create_index(op.f("ix_credit_notes_organization_id"), "credit_notes", ["organization_id"])
    op.create_index("ix_credit_notes_invoice", "credit_notes", ["organization_id", "invoice_id"])

    op.create_table(
        "credit_note_lines",
        sa.Column("credit_note_id", sa.UUID(), nullable=False),
        sa.Column("invoice_line_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit_price_ex_vat", sa.Numeric(12, 2), nullable=False),
        sa.Column("vat_rate", sa.Numeric(5, 2), nullable=False),
        sa.Column("net_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("vat_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("gross_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("returned_to_stock", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        *_tenant_columns(),
        sa.CheckConstraint("quantity > 0", name="ck_credit_note_lines_quantity_positive"),
        sa.CheckConstraint("unit_price_ex_vat >= 0", name="ck_credit_note_lines_price_nonnegative"),
        sa.CheckConstraint("net_amount >= 0 AND vat_amount >= 0", name="ck_credit_note_lines_amounts_nonnegative"),
        sa.CheckConstraint("gross_amount = net_amount + vat_amount", name="ck_credit_note_lines_gross_amount"),
        sa.ForeignKeyConstraint(
            ["organization_id", "credit_note_id"], ["credit_notes.organization_id", "credit_notes.id"], name="fk_credit_note_lines_note", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "invoice_line_id"],
            ["invoice_lines.organization_id", "invoice_lines.id"],
            name="fk_credit_note_lines_invoice_line",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "credit_note_id", "invoice_line_id", name="uq_credit_note_lines_once_per_note"),
    )
    op.create_index(op.f("ix_credit_note_lines_organization_id"), "credit_note_lines", ["organization_id"])
    op.create_index("ix_credit_note_lines_invoice_line", "credit_note_lines", ["organization_id", "invoice_line_id"])

    op.create_table(
        "credit_note_vat_rows",
        sa.Column("credit_note_id", sa.UUID(), nullable=False),
        sa.Column("vat_rate", sa.Numeric(5, 2), nullable=False),
        sa.Column("net_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("vat_amount", sa.Numeric(14, 2), nullable=False),
        *_tenant_columns(),
        sa.ForeignKeyConstraint(
            ["organization_id", "credit_note_id"], ["credit_notes.organization_id", "credit_notes.id"], name="fk_credit_note_vat_rows_note", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "credit_note_id", "vat_rate", name="uq_credit_note_vat_rows_rate"),
    )
    op.create_index(op.f("ix_credit_note_vat_rows_organization_id"), "credit_note_vat_rows", ["organization_id"])

    op.execute(
        """
        CREATE FUNCTION credit_notes_append_only() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               AND current_setting('app.deleting_organization', true) = OLD.organization_id::text THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'credit notes are never changed or deleted' USING ERRCODE = 'check_violation';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION credit_notes_append_only()
            """
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TRIGGER trg_{table}_append_only ON {table}")
    op.execute("DROP FUNCTION credit_notes_append_only()")
    op.drop_index(op.f("ix_credit_note_vat_rows_organization_id"), table_name="credit_note_vat_rows")
    op.drop_table("credit_note_vat_rows")
    op.drop_index("ix_credit_note_lines_invoice_line", table_name="credit_note_lines")
    op.drop_index(op.f("ix_credit_note_lines_organization_id"), table_name="credit_note_lines")
    op.drop_table("credit_note_lines")
    op.drop_index("ix_credit_notes_invoice", table_name="credit_notes")
    op.drop_index(op.f("ix_credit_notes_organization_id"), table_name="credit_notes")
    op.drop_table("credit_notes")
    op.drop_constraint("uq_invoice_lines_organization_id_id", "invoice_lines", type_="unique")
