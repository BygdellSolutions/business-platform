"""TEMPORARY: a migration that always fails, to prove on staging that a failed migration blocks the release (D5, G3).

Reverted right after the proof. Never meant to be kept or to reach any other environment.

Revision ID: b3d5f7a9c0e1
Revises: a2c4e6f81b03
Create Date: 2026-10-15
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b3d5f7a9c0e1"
down_revision: Union[str, Sequence[str], None] = "a2c4e6f81b03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SELECT 1/0")


def downgrade() -> None:
    pass
