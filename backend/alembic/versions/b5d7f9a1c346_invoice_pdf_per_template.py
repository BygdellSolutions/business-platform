"""invoicing: one stored PDF per invoice AND template version

The owner decided (2026-10-08) that the current template is used at all times. Stored PDFs stay as they are (never
changed or deleted); a download of an invoice whose stored PDF was made with an older template stores a new one made
with the current template. Only the uniqueness changes: (organization, invoice) becomes (organization, invoice,
template version).

Revision ID: b5d7f9a1c346
Revises: a4c6e8f0b235
Create Date: 2026-10-08
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b5d7f9a1c346"
down_revision: Union[str, Sequence[str], None] = "a4c6e8f0b235"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("uq_invoice_pdfs_one_per_invoice", "invoice_pdfs", type_="unique")
    op.create_unique_constraint("uq_invoice_pdfs_one_per_template", "invoice_pdfs", ["organization_id", "invoice_id", "template_version"])


def downgrade() -> None:
    # Only possible while no invoice has more than one stored PDF (they are never deleted, so this may refuse).
    op.drop_constraint("uq_invoice_pdfs_one_per_template", "invoice_pdfs", type_="unique")
    op.create_unique_constraint("uq_invoice_pdfs_one_per_invoice", "invoice_pdfs", ["organization_id", "invoice_id"])
