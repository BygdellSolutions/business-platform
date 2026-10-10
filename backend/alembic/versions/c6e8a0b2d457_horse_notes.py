"""equine: notes on a horse

Additive: `horse_notes` (body, who and when; deleted with the horse) and the composite key `uq_horses_organization_id_id`
it refers to.

Revision ID: c6e8a0b2d457
Revises: b5d7f9a1c346
Create Date: 2026-10-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c6e8a0b2d457"
down_revision: Union[str, Sequence[str], None] = "b5d7f9a1c346"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint("uq_horses_organization_id_id", "horses", ["organization_id", "id"])
    op.create_table(
        "horse_notes",
        sa.Column("horse_id", sa.UUID(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.CheckConstraint("length(btrim(body)) > 0", name="ck_horse_notes_body_not_blank"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "horse_id"], ["horses.organization_id", "horses.id"], name="fk_horse_notes_horse", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_horse_notes_organization_id"), "horse_notes", ["organization_id"])
    op.create_index("ix_horse_notes_horse", "horse_notes", ["organization_id", "horse_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_horse_notes_horse", table_name="horse_notes")
    op.drop_index(op.f("ix_horse_notes_organization_id"), table_name="horse_notes")
    op.drop_table("horse_notes")
    op.drop_constraint("uq_horses_organization_id_id", "horses", type_="unique")
