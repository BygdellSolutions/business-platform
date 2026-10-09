"""invoicing: return cases

Additive: `invoice_returns` (state requested / goods_received / approved / rejected / credited, reason, follow-up date,
rejection reason, the credit note that closed it), `invoice_return_lines` (a quantity of an invoice line; whether it
went back into stock) and `invoice_return_events` (the case's log of steps and notes).

Revision ID: a0c2e4f6b891
Revises: f9b1d3e5a780
Create Date: 2026-10-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a0c2e4f6b891"
down_revision: Union[str, Sequence[str], None] = "f9b1d3e5a780"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tenant_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "invoice_returns",
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("follow_up_on", sa.Date(), nullable=False),
        sa.Column("rejection_reason", sa.String(length=500), nullable=True),
        sa.Column("credit_note_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        *_tenant_columns(),
        sa.CheckConstraint("state IN ('requested', 'goods_received', 'approved', 'rejected', 'credited')", name="ck_invoice_returns_state"),
        sa.CheckConstraint("length(btrim(reason)) > 0", name="ck_invoice_returns_reason"),
        sa.CheckConstraint("(state = 'rejected') = (rejection_reason IS NOT NULL)", name="ck_invoice_returns_rejection"),
        sa.CheckConstraint("(state = 'credited') = (credit_note_id IS NOT NULL)", name="ck_invoice_returns_credit_note"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "invoice_id"], ["invoices.organization_id", "invoices.id"], name="fk_invoice_returns_invoice", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["organization_id", "credit_note_id"], ["credit_notes.organization_id", "credit_notes.id"], name="fk_invoice_returns_credit_note", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_invoice_returns_organization_id_id"),
    )
    op.create_index(op.f("ix_invoice_returns_organization_id"), "invoice_returns", ["organization_id"])
    op.create_index("ix_invoice_returns_invoice", "invoice_returns", ["organization_id", "invoice_id"])
    op.create_index(
        "ix_invoice_returns_open",
        "invoice_returns",
        ["organization_id", "follow_up_on"],
        postgresql_where=sa.text("state IN ('requested', 'goods_received', 'approved')"),
    )

    op.create_table(
        "invoice_return_lines",
        sa.Column("return_id", sa.UUID(), nullable=False),
        sa.Column("invoice_line_id", sa.UUID(), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("returned_to_stock", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        *_tenant_columns(),
        sa.CheckConstraint("quantity > 0", name="ck_invoice_return_lines_quantity_positive"),
        sa.ForeignKeyConstraint(
            ["organization_id", "return_id"], ["invoice_returns.organization_id", "invoice_returns.id"], name="fk_invoice_return_lines_return", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "invoice_line_id"], ["invoice_lines.organization_id", "invoice_lines.id"], name="fk_invoice_return_lines_line", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "return_id", "invoice_line_id", name="uq_invoice_return_lines_once"),
    )
    op.create_index(op.f("ix_invoice_return_lines_organization_id"), "invoice_return_lines", ["organization_id"])

    op.create_table(
        "invoice_return_events",
        sa.Column("return_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("note", sa.String(length=2000), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        *_tenant_columns(),
        sa.CheckConstraint(
            "kind IN ('opened', 'note', 'goods_received', 'approved', 'rejected', 'credited', 'follow_up')", name="ck_invoice_return_events_kind"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["organization_id", "return_id"], ["invoice_returns.organization_id", "invoice_returns.id"], name="fk_invoice_return_events_return", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_invoice_return_events_organization_id"), "invoice_return_events", ["organization_id"])
    op.create_index("ix_invoice_return_events_return", "invoice_return_events", ["organization_id", "return_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_invoice_return_events_return", table_name="invoice_return_events")
    op.drop_index(op.f("ix_invoice_return_events_organization_id"), table_name="invoice_return_events")
    op.drop_table("invoice_return_events")
    op.drop_index(op.f("ix_invoice_return_lines_organization_id"), table_name="invoice_return_lines")
    op.drop_table("invoice_return_lines")
    op.drop_index("ix_invoice_returns_open", table_name="invoice_returns")
    op.drop_index("ix_invoice_returns_invoice", table_name="invoice_returns")
    op.drop_index(op.f("ix_invoice_returns_organization_id"), table_name="invoice_returns")
    op.drop_table("invoice_returns")
