"""organizations: contact and payment details, F-tax and document language

Additive and nullable: nothing is filled in for existing organizations. CHECKs: IBAN and BIC shapes (international
standards), payment terms 0-365 days, document language 'sv' or 'en'.

Revision ID: e2a4c6d8f013
Revises: d1f3b5c7e902
Create Date: 2026-10-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2a4c6d8f013"
down_revision: Union[str, Sequence[str], None] = "d1f3b5c7e902"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (
    ("phone", sa.String(length=64)),
    ("email", sa.String(length=255)),
    ("website", sa.String(length=255)),
    ("bankgiro", sa.String(length=32)),
    ("plusgiro", sa.String(length=32)),
    ("iban", sa.String(length=34)),
    ("bic", sa.String(length=11)),
    ("payment_terms_days", sa.SmallInteger()),
    ("approved_for_f_tax", sa.Boolean()),
    ("document_language", sa.String(length=2)),
)
CHECKS = (
    ("ck_organizations_iban_shape", "iban IS NULL OR iban ~ '^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$'"),
    ("ck_organizations_bic_shape", "bic IS NULL OR bic ~ '^[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?$'"),
    ("ck_organizations_payment_terms_range", "payment_terms_days IS NULL OR (payment_terms_days >= 0 AND payment_terms_days <= 365)"),
    ("ck_organizations_document_language", "document_language IS NULL OR document_language IN ('sv', 'en')"),
)


def upgrade() -> None:
    for name, type_ in COLUMNS:
        op.add_column("organizations", sa.Column(name, type_, nullable=True))
    for name, condition in CHECKS:
        op.create_check_constraint(name, "organizations", condition)


def downgrade() -> None:
    for name, _ in CHECKS:
        op.drop_constraint(name, "organizations", type_="check")
    for name, _ in reversed(COLUMNS):
        op.drop_column("organizations", name)
