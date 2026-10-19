"""organization logo: one immutable row per uploaded version, and the organization's current one

* `organization_logos`: the normalized PNG, its hash and size. Insert-only, so an issued invoice can always find the version it was
  issued with (the issuer snapshot records its id and hash).
* `organizations.logo_id`: the current version, NULL for no logo.

Revision ID: b1d3f5a7c9e2
Revises: a9c1e3f5b7d8
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b1d3f5a7c9e2"
down_revision: Union[str, Sequence[str], None] = "a9c1e3f5b7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organization_logos",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_organization_logos_organization_id", "organization_logos", ["organization_id"])
    op.add_column("organizations", sa.Column("logo_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_organizations_logo", "organizations", "organization_logos", ["logo_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_organizations_logo", "organizations", type_="foreignkey")
    op.drop_column("organizations", "logo_id")
    op.drop_table("organization_logos")
