"""invoice numbers: a series starts at 1001 (like orders)

New series start at 1001 (`app.modules.invoicing.numbering.FIRST_NUMBER`). Existing counters below 1001 move up to
1001, so the next invoice or credit note of such an organization is 1001: numbers stay unique and increasing within
the series, and invoices already issued keep their numbers (an issued invoice never changes). Decided 2026-10-10,
before any production data existed; on a database with real issued invoices this jump would be a visible gap in the
series and should be documented for the bookkeeping.

Downgrade: nothing to undo. Lowering a counter could hand out a number again, so the counters stay where they are.

Revision ID: f6b8d0e2a346
Revises: e4a6c8d0f235
Create Date: 2026-10-10
"""

from typing import Sequence, Union

from alembic import op

revision: str = "f6b8d0e2a346"
down_revision: Union[str, Sequence[str], None] = "e4a6c8d0f235"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE invoice_counters SET next_number = 1001 WHERE next_number < 1001")


def downgrade() -> None:
    pass
