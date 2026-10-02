import uuid
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import TenantOwned

TEXT_MAX_LENGTH = 2000


class FieldType(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    BOOLEAN = "boolean"
    SELECT = "select"
    REFERENCE = "reference"


def _in_list(column: str, values) -> str:
    return f"{column} IN ('" + "', '".join(values) + "')"


class CustomFieldDefinition(TenantOwned, Base):
    """An organization-defined field on a registered entity type.

    Structural properties (`entity_type`, `key`, `field_type`, `reference_source`,
    the dependency) never change after creation; label, required, position, enabled and
    the show_* flags do. Definitions are disabled, never deleted.

    `show_on_invoice` means: this field is ELIGIBLE to be snapshotted when an invoice is
    created. Invoices must store the rendered label and value they need at issuance and
    never resolve current definitions or references when displaying an existing invoice.
    """

    __tablename__ = "custom_field_definitions"
    __table_args__ = (
        # Targets of the composite foreign keys below and in the other tables.
        UniqueConstraint("organization_id", "id", name="uq_custom_field_definitions_org_id"),
        UniqueConstraint(
            "organization_id", "entity_type", "key", name="uq_custom_field_definitions_entity_key"
        ),
        UniqueConstraint(
            "organization_id", "id", "field_type", name="uq_custom_field_definitions_id_type"
        ),
        UniqueConstraint(
            "organization_id",
            "id",
            "entity_type",
            "field_type",
            name="uq_custom_field_definitions_id_entity_type",
        ),
        # A dependent field's parent is another definition of the SAME organization.
        ForeignKeyConstraint(
            ["organization_id", "depends_on_definition_id"],
            ["custom_field_definitions.organization_id", "custom_field_definitions.id"],
            ondelete="RESTRICT",
            name="fk_custom_field_definitions_depends_on_same_organization",
        ),
        CheckConstraint(_in_list("field_type", FieldType), name="ck_custom_field_definitions_type"),
        CheckConstraint("key ~ '^[a-z][a-z0-9_]{0,39}$'", name="ck_custom_field_definitions_key"),
        CheckConstraint(
            "(field_type = 'reference') = (reference_source IS NOT NULL)",
            name="ck_custom_field_definitions_reference_source",
        ),
        CheckConstraint(
            "(depends_on_definition_id IS NULL) = (depends_on_filter IS NULL)",
            name="ck_custom_field_definitions_dependency_pair",
        ),
        CheckConstraint(
            "depends_on_definition_id IS NULL OR field_type = 'reference'",
            name="ck_custom_field_definitions_dependency_only_references",
        ),
    )

    entity_type: Mapped[str] = mapped_column(String(64))  # registry key, validated on write
    key: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(100))
    field_type: Mapped[str] = mapped_column(String(16))
    required: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    position: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    show_in_form: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    show_in_table: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    show_on_invoice: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    reference_source: Mapped[str | None] = mapped_column(String(64))  # registry key
    depends_on_definition_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    depends_on_filter: Mapped[str | None] = mapped_column(String(64))  # a registry filter key


class CustomFieldOption(TenantOwned, Base):
    """A choice of a `select` field. Its UUID is the stable identifier values store, so
    relabelling or disabling an option never touches existing values."""

    __tablename__ = "custom_field_options"
    __table_args__ = (
        # Target of the value rows' foreign key: "this option belongs to this definition".
        UniqueConstraint(
            "organization_id", "definition_id", "id", name="uq_custom_field_options_definition_id"
        ),
        # `field_type` is a constant that pins the parent to a select definition.
        ForeignKeyConstraint(
            ["organization_id", "definition_id", "field_type"],
            [
                "custom_field_definitions.organization_id",
                "custom_field_definitions.id",
                "custom_field_definitions.field_type",
            ],
            ondelete="RESTRICT",
            name="fk_custom_field_options_definition_same_organization",
        ),
        CheckConstraint("field_type = 'select'", name="ck_custom_field_options_select_only"),
        Index("ix_custom_field_options_organization_definition", "organization_id", "definition_id"),
    )

    definition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    field_type: Mapped[str] = mapped_column(
        String(16), default=FieldType.SELECT, server_default=text("'select'")
    )
    label: Mapped[str] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class CustomFieldValue(TenantOwned, Base):
    """One value of one field on one record, in a column of the matching type.

    One polymorphic table with typed columns: real types and indexes, no JSON. Composite
    foreign keys make the database check that the row's type matches its definition and
    that an option belongs to that definition. `entity_id` and `value_reference_id` point
    at records of registered entity types, so they cannot be foreign keys; the API validates
    them through the registry and a delete guard protects referenced records.
    "No value" is the absence of a row.
    """

    __tablename__ = "custom_field_values"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "definition_id", "entity_id", name="uq_custom_field_values_field_entity"
        ),
        ForeignKeyConstraint(
            ["organization_id", "definition_id", "entity_type", "field_type"],
            [
                "custom_field_definitions.organization_id",
                "custom_field_definitions.id",
                "custom_field_definitions.entity_type",
                "custom_field_definitions.field_type",
            ],
            ondelete="RESTRICT",
            name="fk_custom_field_values_definition_same_organization",
        ),
        # MATCH SIMPLE: skipped when value_option_id is NULL.
        ForeignKeyConstraint(
            ["organization_id", "definition_id", "value_option_id"],
            [
                "custom_field_options.organization_id",
                "custom_field_options.definition_id",
                "custom_field_options.id",
            ],
            ondelete="RESTRICT",
            name="fk_custom_field_values_option_of_definition",
        ),
        CheckConstraint(
            "num_nonnulls(value_text, value_number, value_date, value_boolean,"
            " value_option_id, value_reference_id) = 1 AND coalesce(CASE field_type"
            " WHEN 'text' THEN value_text IS NOT NULL"
            " WHEN 'number' THEN value_number IS NOT NULL"
            " WHEN 'date' THEN value_date IS NOT NULL"
            " WHEN 'boolean' THEN value_boolean IS NOT NULL"
            " WHEN 'select' THEN value_option_id IS NOT NULL"
            " WHEN 'reference' THEN value_reference_id IS NOT NULL END, false)",
            name="ck_custom_field_values_typed_value",
        ),
        Index("ix_custom_field_values_organization_entity", "organization_id", "entity_type", "entity_id"),
        Index(
            "ix_custom_field_values_organization_reference",
            "organization_id",
            "value_reference_id",
            postgresql_where=text("value_reference_id IS NOT NULL"),
        ),
        Index(
            "ix_custom_field_values_organization_option",
            "organization_id",
            "value_option_id",
            postgresql_where=text("value_option_id IS NOT NULL"),
        ),
    )

    definition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    entity_type: Mapped[str] = mapped_column(String(64))
    field_type: Mapped[str] = mapped_column(String(16))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    value_text: Mapped[str | None] = mapped_column(String(TEXT_MAX_LENGTH))
    value_number: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    value_date: Mapped[date | None] = mapped_column(Date)
    value_boolean: Mapped[bool | None] = mapped_column(Boolean)
    value_option_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    value_reference_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
