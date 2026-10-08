import uuid
from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator, model_validator

from app.models import ItemType
from app.schemas.money import DiscountPercentIn, MoneyIn, MoneyOut, PercentIn, PercentOut

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
    # The temporary discount active today (in the organization's time zone), if any. The price above never changes.
    current_discount: "ItemDiscountRead | None" = None


class ItemDiscountCreate(BaseModel):
    """A temporary discount: `percent` off from `starts_on` to `ends_on` (inclusive; open-ended without it)."""

    model_config = ConfigDict(extra="forbid")

    percent: DiscountPercentIn
    starts_on: date
    ends_on: date | None = None
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)] | None = None

    @model_validator(mode="after")
    def period_in_order(self):
        if self.ends_on is not None and self.ends_on < self.starts_on:
            raise ValueError("ends_on cannot be before starts_on")
        return self


class ItemDiscountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_id: uuid.UUID
    percent: PercentOut
    starts_on: date
    ends_on: date | None
    note: str | None
    created_at: datetime
    created_by: uuid.UUID | None


ItemRead.model_rebuild()
