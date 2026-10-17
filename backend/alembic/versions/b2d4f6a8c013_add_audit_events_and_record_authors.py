"""add audit events and record authors

Revision ID: b2d4f6a8c013
Revises: a1c3e5f7b902
Create Date: 2026-10-08 02:00:47.343152

`audit_events` is the append-only change history of business records. `created_by` / `updated_by` are added to
the records people edit; existing rows keep NULL ("not recorded"): no author is guessed for the past.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "b2d4f6a8c013"
down_revision: Union[str, Sequence[str], None] = "a1c3e5f7b902"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

AUTHORED_TABLES = ("customers", "items", "horses", "transactions", "transaction_lines", "invoices")


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("context_type", sa.String(length=64), nullable=True),
        sa.Column("context_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], name="fk_audit_events_actor", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_audit_events_organization", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
    )
    op.create_index("ix_audit_events_context", "audit_events", ["organization_id", "context_type", "context_id", "occurred_at"])
    op.create_index("ix_audit_events_entity", "audit_events", ["organization_id", "entity_type", "entity_id", "occurred_at"])

    # History is never edited. Rows go away only together with their organization: a deletion that sets
    # app.deleting_organization to that organization's id inside its own transaction.
    op.execute(
        """
        CREATE FUNCTION audit_events_append_only() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               AND current_setting('app.deleting_organization', true) = OLD.organization_id::text THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'audit events are append-only' USING ERRCODE = 'check_violation';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_events_append_only
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION audit_events_append_only()
        """
    )

    for table in AUTHORED_TABLES:
        for column in ("created_by", "updated_by"):
            op.add_column(table, sa.Column(column, sa.UUID(), nullable=True))
            op.create_foreign_key(f"fk_{table}_{column}", table, "users", [column], ["id"], ondelete="RESTRICT")


def downgrade() -> None:
    for table in AUTHORED_TABLES:
        op.drop_column(table, "updated_by")
        op.drop_column(table, "created_by")
    op.execute("DROP TRIGGER trg_audit_events_append_only ON audit_events")
    op.execute("DROP FUNCTION audit_events_append_only()")
    op.drop_index("ix_audit_events_entity", table_name="audit_events")
    op.drop_index("ix_audit_events_context", table_name="audit_events")
    op.drop_table("audit_events")
