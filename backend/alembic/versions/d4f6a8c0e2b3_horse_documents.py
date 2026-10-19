"""horse documents: PDFs and pictures kept with a horse

One row per file, in PostgreSQL like the organization logo. The foreign key is composite (organization_id, horse_id), so the
database itself refuses a document that points at another organization's horse; the size and type are CHECKed, and the stored
size must equal the length of the bytes.

Revision ID: d4f6a8c0e2b3
Revises: b1d3f5a7c9e2
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4f6a8c0e2b3"
down_revision: Union[str, Sequence[str], None] = "b1d3f5a7c9e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MAX_BYTES = 10 * 1024 * 1024


def upgrade() -> None:
    op.create_table(
        "horse_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("horse_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(200), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "horse_id"], ["horses.organization_id", "horses.id"], ondelete="CASCADE", name="fk_horse_documents_horse"),
        sa.CheckConstraint("length(btrim(filename)) > 0", name="ck_horse_documents_filename_not_blank"),
        sa.CheckConstraint("content_type IN ('application/pdf', 'image/jpeg', 'image/png', 'image/webp')", name="ck_horse_documents_content_type"),
        sa.CheckConstraint(f"byte_size BETWEEN 1 AND {MAX_BYTES}", name="ck_horse_documents_byte_size"),
        sa.CheckConstraint("octet_length(content) = byte_size", name="ck_horse_documents_size_matches_content"),
    )
    op.create_index("ix_horse_documents_organization_id", "horse_documents", ["organization_id"])
    op.create_index("ix_horse_documents_horse", "horse_documents", ["organization_id", "horse_id", "created_at"])


def downgrade() -> None:
    op.drop_table("horse_documents")
