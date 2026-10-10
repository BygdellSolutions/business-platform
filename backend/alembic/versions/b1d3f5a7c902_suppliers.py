"""suppliers: a register of suppliers, chosen on incoming stock

Adds `suppliers` (name, contact person, email, phone, our customer number at the supplier, active, the business
profile, authors) and `incoming_stock.supplier_id` (composite foreign key, same organization, RESTRICT).

Data: every distinct supplier name typed on incoming stock so far becomes one supplier of that organization (names
equal after trimming and ignoring case are one supplier, named as first typed), and its deliveries point to it. Then
the free-text column is dropped; nothing is lost, the names now live in `suppliers`. The downgrade puts the names back.

Revision ID: b1d3f5a7c902
Revises: a0c2e4f6b891
Create Date: 2026-10-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b1d3f5a7c902"
down_revision: Union[str, Sequence[str], None] = "a0c2e4f6b891"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "suppliers",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("contact_person", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("our_customer_number", sa.String(length=64), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("address_line1", sa.String(length=255), nullable=True),
        sa.Column("address_line2", sa.String(length=255), nullable=True),
        sa.Column("postal_code", sa.String(length=32), nullable=True),
        sa.Column("city", sa.String(length=128), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("registration_number", sa.String(length=64), nullable=True),
        sa.Column("vat_number", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("country_code IS NULL OR country_code ~ '^[A-Z]{2}$'", name="ck_suppliers_country_code_shape"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_suppliers_organization_id_id"),
    )
    op.create_index(op.f("ix_suppliers_organization_id"), "suppliers", ["organization_id"])

    op.add_column("incoming_stock", sa.Column("supplier_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_incoming_stock_supplier", "incoming_stock", "suppliers", ["organization_id", "supplier_id"], ["organization_id", "id"], ondelete="RESTRICT"
    )
    op.create_index("ix_incoming_stock_supplier", "incoming_stock", ["organization_id", "supplier_id"])

    # One supplier per organization and distinct typed name, then link the deliveries.
    op.execute(
        """
        INSERT INTO suppliers (organization_id, name)
        SELECT DISTINCT ON (organization_id, lower(btrim(supplier))) organization_id, btrim(supplier)
        FROM incoming_stock
        WHERE supplier IS NOT NULL AND btrim(supplier) <> ''
        ORDER BY organization_id, lower(btrim(supplier)), created_at
        """
    )
    op.execute(
        """
        UPDATE incoming_stock AS i SET supplier_id = s.id
        FROM suppliers AS s
        WHERE s.organization_id = i.organization_id AND lower(s.name) = lower(btrim(i.supplier))
        """
    )
    op.drop_column("incoming_stock", "supplier")


def downgrade() -> None:
    op.add_column("incoming_stock", sa.Column("supplier", sa.String(length=255), nullable=True))
    op.execute(
        """
        UPDATE incoming_stock AS i SET supplier = s.name
        FROM suppliers AS s
        WHERE s.organization_id = i.organization_id AND s.id = i.supplier_id
        """
    )
    op.drop_index("ix_incoming_stock_supplier", table_name="incoming_stock")
    op.drop_constraint("fk_incoming_stock_supplier", "incoming_stock", type_="foreignkey")
    op.drop_column("incoming_stock", "supplier_id")
    op.drop_index(op.f("ix_suppliers_organization_id"), table_name="suppliers")
    op.drop_table("suppliers")
