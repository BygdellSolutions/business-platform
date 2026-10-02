import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.modules.custom_fields.models import FieldType

Key = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}$")]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]

MAX_BULK_ENTITIES = 200

# As with every tenant-owned resource: no organization_id field and extra="forbid".


class ReferenceConfig(BaseModel):
    """A reference field: which registered entity it points at and, optionally, which
    other field of the same record it depends on and how that narrows its choices."""

    model_config = ConfigDict(extra="forbid")

    source: str
    depends_on: Key | None = None  # key of another reference field on the same entity type
    filter: str | None = None  # a filter key the source entity registered

    @model_validator(mode="after")
    def dependency_is_complete(self):
        if (self.depends_on is None) != (self.filter is None):
            raise ValueError("depends_on and filter must be given together")
        return self


class OptionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Label


class OptionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Label | None = None
    position: int | None = None
    enabled: bool | None = None

    @field_validator("label", "position", "enabled")
    @classmethod
    def not_null(cls, value):
        if value is None:
            raise ValueError("may not be null")
        return value


class OptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    position: int
    enabled: bool


class DefinitionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_type: str
    key: Key
    label: Label
    field_type: FieldType
    required: bool = False
    position: int | None = None
    show_in_form: bool = True
    show_in_table: bool = False
    show_on_invoice: bool = False  # eligible to be snapshotted on a future invoice
    reference: ReferenceConfig | None = None
    options: list[OptionCreate] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def type_specific_parts(self):
        if (self.field_type == FieldType.REFERENCE) != (self.reference is not None):
            raise ValueError("reference is required for, and only for, reference fields")
        if self.options is not None and self.field_type != FieldType.SELECT:
            raise ValueError("options are only for select fields")
        return self


class DefinitionUpdate(BaseModel):
    """Only the properties that are safe to change once values may exist."""

    model_config = ConfigDict(extra="forbid")

    label: Label | None = None
    required: bool | None = None
    position: int | None = None
    enabled: bool | None = None
    show_in_form: bool | None = None
    show_in_table: bool | None = None
    show_on_invoice: bool | None = None

    @field_validator(
        "label", "required", "position", "enabled", "show_in_form", "show_in_table", "show_on_invoice"
    )
    @classmethod
    def not_null(cls, value):
        if value is None:
            raise ValueError("may not be null")
        return value


class ReferenceRead(BaseModel):
    source: str
    depends_on: str | None  # key of the field this one depends on
    filter: str | None


class DefinitionRead(BaseModel):
    id: uuid.UUID
    entity_type: str
    key: str
    label: str
    field_type: FieldType
    required: bool
    position: int
    enabled: bool
    show_in_form: bool
    show_in_table: bool
    show_on_invoice: bool
    reference: ReferenceRead | None
    options: list[OptionRead] | None  # select fields only
    created_at: datetime
    updated_at: datetime


class FilterRead(BaseModel):
    key: str
    label: str
    references: str


class EntityTypeRead(BaseModel):
    key: str
    label: str
    custom_fields: bool  # may definitions target it?
    referenceable: bool  # may reference fields point at it?
    filters: list[FilterRead]


class ChoiceRead(BaseModel):
    id: uuid.UUID
    label: str
    active: bool


class ValueRead(BaseModel):
    """A set value. `value` is what is stored (text, decimal string, ISO date, bool, or the
    option/record UUID); `display` is resolved live for options and references."""

    key: str
    label: str
    field_type: FieldType
    value: Any
    display: str | None
    active: bool | None  # options and references: still selectable?
    missing: bool  # a reference whose target no longer exists


class EntityValuesRead(BaseModel):
    entity_type: str
    entity_id: uuid.UUID
    values: list[ValueRead]
    missing_required: list[str]  # keys of enabled required fields without a value


class BulkValuesRead(BaseModel):
    entity_type: str
    entities: dict[uuid.UUID, list[ValueRead]]


class ValuesPatch(BaseModel):
    """`{"values": {"<key>": <value or null>}}`. Null clears a value."""

    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any]
