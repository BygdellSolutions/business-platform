import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from app.models import ItemType
from app.schemas.money import MoneyIn, MoneyOut, PercentIn, PercentOut

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Unit = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]

# As with Customers: no organization_id field, and extra="forbid" turns a
# client-supplied one into a 422. The organization comes from the tenant context.


class ItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ItemType
    name: Name
    description: Description | None = None
    unit: Unit
    price_ex_vat: MoneyIn
    vat_rate: PercentIn
    active: bool = True


class ItemUpdate(BaseModel):
    """Partial update: only fields present in the request are changed."""

    model_config = ConfigDict(extra="forbid")

    type: ItemType | None = None
    name: Name | None = None
    description: Description | None = None
    unit: Unit | None = None
    price_ex_vat: MoneyIn | None = None
    vat_rate: PercentIn | None = None
    active: bool | None = None

    @field_validator("type", "name", "unit", "price_ex_vat", "vat_rate", "active")
    @classmethod
    def not_null(cls, value):
        # Runs only for fields that were sent; these columns are NOT NULL.
        if value is None:
            raise ValueError("may not be null")
        return value


class ItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: ItemType
    name: str
    description: str | None
    unit: str
    price_ex_vat: MoneyOut
    vat_rate: PercentOut
    active: bool
    created_at: datetime
    updated_at: datetime
    # Who created it and who changed it last (null: not recorded, e.g. before authors were kept).
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None
