"""record numbers: orders (from 1001), customers, suppliers, horses and catalog items (from 1) get a number per organization

Adds `record_counters` (organization, series, next number) and a `number` column on `transactions`, `customers`,
`suppliers`, `horses` and `items`, unique per organization (an item's number is not its article number, SKU). A BEFORE INSERT trigger hands the number out from the counter row
of (organization, table), atomically (INSERT ... ON CONFLICT DO UPDATE ... RETURNING locks that row until the
transaction ends), so every path that inserts (API, seed, tests, raw SQL) gets one, and two inserts never get the
same. The same trigger refuses changing a number afterwards. A rolled-back insert may leave a gap; these are
references for people, not a legal series.

Existing rows are numbered per organization in creation order (orders 1001, 1002...; the others 1, 2...), and each
counter continues after them. The first number of each table is the trigger's argument.

`invoice_transactions.transaction_number` snapshots the order's number when an invoice is created (draft invoices
are filled in here; issued invoices stay as issued: their sources simply had no number).

Revision ID: e4a6c8d0f235
Revises: d3f5b7c9e124
Create Date: 2026-10-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e4a6c8d0f235"
down_revision: Union[str, Sequence[str], None] = "d3f5b7c9e124"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Table -> its first number (decided by the owner 2026-10-10: orders look established from 1001, the rest count from 1).
NUMBERED = {"transactions": 1001, "customers": 1, "suppliers": 1, "horses": 1, "items": 1}


def upgrade() -> None:
    op.create_table(
        "record_counters",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("series", sa.String(length=32), nullable=False),
        sa.Column("next_number", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("next_number >= 1", name="ck_record_counters_next_positive"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("organization_id", "series", name="pk_record_counters"),
    )
    op.execute(
        """
        CREATE FUNCTION assign_record_number() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                IF NEW.number IS DISTINCT FROM OLD.number THEN
                    RAISE EXCEPTION 'a record number never changes' USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.number IS NULL THEN
                INSERT INTO record_counters (organization_id, series, next_number)
                VALUES (NEW.organization_id, TG_TABLE_NAME, TG_ARGV[0]::bigint + 1)
                ON CONFLICT (organization_id, series) DO UPDATE SET next_number = record_counters.next_number + 1
                RETURNING next_number - 1 INTO NEW.number;  -- the first insert of a series gets TG_ARGV[0]
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    for table, first in NUMBERED.items():
        op.add_column(table, sa.Column("number", sa.BigInteger(), nullable=True))
        op.execute(
            f"""
            UPDATE {table} t SET number = n.number
            FROM (SELECT id, {first - 1} + row_number() OVER (PARTITION BY organization_id ORDER BY created_at, id) AS number FROM {table}) n
            WHERE t.id = n.id
            """
        )
        op.execute(
            f"""
            INSERT INTO record_counters (organization_id, series, next_number)
            SELECT organization_id, '{table}', max(number) + 1 FROM {table} GROUP BY organization_id
            """
        )
        op.alter_column(table, "number", nullable=False)
        op.create_unique_constraint(f"uq_{table}_organization_number", table, ["organization_id", "number"])
        op.execute(
            f"CREATE TRIGGER {table}_record_number BEFORE INSERT OR UPDATE OF number ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION assign_record_number({first})"
        )

    op.add_column("invoice_transactions", sa.Column("transaction_number", sa.BigInteger(), nullable=True))
    # The immutability trigger lets a DRAFT's link change only in its snapshot fields, so it would refuse this
    # one-time fill (it did on staging). It is switched off for this statement only, inside the migration's
    # transaction; issued invoices are excluded by the WHERE clause, not by the trigger.
    op.execute("ALTER TABLE invoice_transactions DISABLE TRIGGER trg_invoice_transactions_immutability")
    op.execute(
        """
        UPDATE invoice_transactions l SET transaction_number = t.number
        FROM transactions t, invoices i
        WHERE t.organization_id = l.organization_id AND t.id = l.transaction_id
          AND i.organization_id = l.organization_id AND i.id = l.invoice_id AND i.status = 'draft'
        """
    )
    op.execute("ALTER TABLE invoice_transactions ENABLE TRIGGER trg_invoice_transactions_immutability")


def downgrade() -> None:
    op.drop_column("invoice_transactions", "transaction_number")
    for table in NUMBERED:
        op.execute(f"DROP TRIGGER {table}_record_number ON {table}")
        op.drop_constraint(f"uq_{table}_organization_number", table, type_="unique")
        op.drop_column(table, "number")
    op.execute("DROP FUNCTION assign_record_number()")
    op.drop_table("record_counters")
