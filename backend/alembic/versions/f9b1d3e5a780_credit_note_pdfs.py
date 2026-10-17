"""invoicing: frozen credit note PDFs

Additive: `credit_note_pdfs`, one per credit note and template version, exactly like `invoice_pdfs` (bytes, size,
hashes, renderer). Append-only through the credit notes' trigger function (no UPDATE or DELETE except while the
organization itself is being deleted).

Revision ID: f9b1d3e5a780
Revises: e8a0c2d4f679
Create Date: 2026-10-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f9b1d3e5a780"
down_revision: Union[str, Sequence[str], None] = "e8a0c2d4f679"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "credit_note_pdfs",
        sa.Column("credit_note_id", sa.UUID(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("renderer", sa.String(length=255), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("byte_size > 0 AND byte_size = octet_length(content)", name="ck_credit_note_pdfs_byte_size"),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$' AND source_sha256 ~ '^[0-9a-f]{64}$'", name="ck_credit_note_pdfs_hash_shape"),
        sa.CheckConstraint("sha256 = encode(sha256(content), 'hex')", name="ck_credit_note_pdfs_sha256_matches"),
        sa.CheckConstraint("substring(content from 1 for 5) = decode('255044462d', 'hex')", name="ck_credit_note_pdfs_is_pdf"),
        sa.CheckConstraint("template_version >= 1 AND renderer <> ''", name="ck_credit_note_pdfs_provenance"),
        sa.ForeignKeyConstraint(
            ["organization_id", "credit_note_id"], ["credit_notes.organization_id", "credit_notes.id"], name="fk_credit_note_pdfs_note", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "credit_note_id", "template_version", name="uq_credit_note_pdfs_one_per_template"),
    )
    op.create_index(op.f("ix_credit_note_pdfs_organization_id"), "credit_note_pdfs", ["organization_id"])
    op.execute(
        """
        CREATE TRIGGER trg_credit_note_pdfs_append_only
        BEFORE UPDATE OR DELETE ON credit_note_pdfs
        FOR EACH ROW EXECUTE FUNCTION credit_notes_append_only()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_credit_note_pdfs_append_only ON credit_note_pdfs")
    op.drop_index(op.f("ix_credit_note_pdfs_organization_id"), table_name="credit_note_pdfs")
    op.drop_table("credit_note_pdfs")
