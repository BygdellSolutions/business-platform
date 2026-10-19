"""invoicing by delivery: an order line is invoiced as it is delivered, possibly over several invoices

Decided by the owner on 2026-10-11 (docs/architecture.md, "Invoicing by delivery"):

- `invoice_transactions` loses "an order is on one invoice" and `invoice_lines` loses "a line is invoiced once"; both
  become plain lookup indexes. That an order has at most one draft is checked under the order's row lock.
- `invoice_transactions` gains `completes_order`: with this invoice every line of the order is invoiced in full (so
  where an order stands is read from Invoicing's own tables). Earlier invoices always took the whole order: true.
- `invoice_lines` gains `ordered_quantity` (the order line's quantity when the draft was built) and
  `backordered_quantity` (what of it was still waiting for delivery). Lines invoiced before this migration were whole
  order lines: their `ordered_quantity` stays NULL ("invoiced whole") rather than a backfilled value, and no row of an
  issued invoice is updated.
- `invoice_lines_within_order`: a trigger refusing an invoice line that would put more of its source line, over all
  invoices, than the order line's quantity (the application refuses more than was DELIVERED, which only Inventory
  knows; this is the backstop for what can be checked in the database).
- `invoice_holds`: "wait until everything is delivered", per order.

Downgrade refuses while an order or an order line is on more than one invoice (the old unique keys cannot hold).

Revision ID: b8d0f2a4c613
Revises: a7c9e1f3b457
Create Date: 2026-10-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b8d0f2a4c613"
down_revision: Union[str, Sequence[str], None] = "a7c9e1f3b457"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("uq_invoice_transactions_source_once", "invoice_transactions", type_="unique")
    op.create_index("ix_invoice_transactions_organization_transaction", "invoice_transactions", ["organization_id", "transaction_id"])
    # Whether the invoice completes its order; earlier invoices always took the whole order (a default, no row update).
    op.add_column("invoice_transactions", sa.Column("completes_order", sa.Boolean(), nullable=False, server_default=sa.text("true")))
    op.drop_constraint("uq_invoice_lines_source_line_once", "invoice_lines", type_="unique")
    op.create_index("ix_invoice_lines_organization_source_line", "invoice_lines", ["organization_id", "source_line_id"])

    op.add_column("invoice_lines", sa.Column("ordered_quantity", sa.Numeric(12, 3), nullable=True))
    op.add_column("invoice_lines", sa.Column("backordered_quantity", sa.Numeric(12, 3), nullable=False, server_default=sa.text("0")))
    op.create_check_constraint("ck_invoice_lines_backordered_nonnegative", "invoice_lines", "backordered_quantity >= 0")
    op.create_check_constraint(
        "ck_invoice_lines_within_ordered", "invoice_lines", "ordered_quantity IS NULL OR ordered_quantity >= quantity + backordered_quantity"
    )

    op.execute(
        """
        CREATE FUNCTION invoice_lines_within_order() RETURNS trigger AS $$
        DECLARE
            ordered numeric;
            invoiced numeric;
        BEGIN
            -- The order line, locked: two invoices of the same line serialize here.
            SELECT quantity INTO ordered FROM transaction_lines
             WHERE organization_id = NEW.organization_id AND id = NEW.source_line_id
             FOR NO KEY UPDATE;
            SELECT COALESCE(sum(quantity), 0) INTO invoiced FROM invoice_lines
             WHERE organization_id = NEW.organization_id AND source_line_id = NEW.source_line_id
               AND id <> NEW.id;
            IF invoiced + NEW.quantity > ordered THEN
                RAISE EXCEPTION 'an order line cannot be invoiced for more than was ordered'
                    USING ERRCODE = 'check_violation', CONSTRAINT = 'invoice_lines_within_order';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_invoice_lines_within_order
        BEFORE INSERT OR UPDATE OF quantity, source_line_id ON invoice_lines
        FOR EACH ROW EXECUTE FUNCTION invoice_lines_within_order()
        """
    )

    op.create_table(
        "invoice_holds",
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["organization_id", "transaction_id"],
            ["transactions.organization_id", "transactions.id"],
            ondelete="CASCADE",
            name="fk_invoice_holds_transaction",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "transaction_id", name="uq_invoice_holds_transaction"),
    )
    op.create_index(op.f("ix_invoice_holds_organization_id"), "invoice_holds", ["organization_id"], unique=False)


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM invoice_transactions GROUP BY organization_id, transaction_id HAVING count(*) > 1)
               OR EXISTS (SELECT 1 FROM invoice_lines GROUP BY organization_id, source_line_id HAVING count(*) > 1) THEN
                RAISE EXCEPTION 'cannot downgrade: an order is invoiced over several invoices';
            END IF;
        END $$
        """
    )
    op.drop_index(op.f("ix_invoice_holds_organization_id"), table_name="invoice_holds")
    op.drop_table("invoice_holds")
    op.execute("DROP TRIGGER trg_invoice_lines_within_order ON invoice_lines")
    op.execute("DROP FUNCTION invoice_lines_within_order()")
    op.drop_constraint("ck_invoice_lines_within_ordered", "invoice_lines", type_="check")
    op.drop_constraint("ck_invoice_lines_backordered_nonnegative", "invoice_lines", type_="check")
    op.drop_column("invoice_lines", "backordered_quantity")
    op.drop_column("invoice_lines", "ordered_quantity")
    op.drop_index("ix_invoice_lines_organization_source_line", table_name="invoice_lines")
    op.create_unique_constraint("uq_invoice_lines_source_line_once", "invoice_lines", ["organization_id", "source_line_id"])
    op.drop_column("invoice_transactions", "completes_order")
    op.drop_index("ix_invoice_transactions_organization_transaction", table_name="invoice_transactions")
    op.create_unique_constraint("uq_invoice_transactions_source_once", "invoice_transactions", ["organization_id", "transaction_id"])
