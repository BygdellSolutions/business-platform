import uuid
from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.modules.equine.models import MIN_BIRTH_YEAR, HorseSex

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Breed = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
# strict: only a real JSON integer, not "2010", 2010.0 or true.
BirthYear = Annotated[int, Field(strict=True, ge=MIN_BIRTH_YEAR)]

# As with other tenant-owned resources: no organization_id field and extra="forbid".
# There is deliberately no billing field: billing belongs to the transaction.


def _not_in_the_future(year: int | None) -> int | None:
    if year is not None and year > date.today().year:
        raise ValueError("birth_year cannot be in the future")
    return year


class HorseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    owner_customer_id: uuid.UUID
    stable_customer_id: uuid.UUID | None = None
    birth_year: BirthYear | None = None
    sex: HorseSex | None = None
    breed: Breed | None = None
    active: bool = True

    _check_year = field_validator("birth_year")(_not_in_the_future)


class HorseUpdate(BaseModel):
    """Partial update: only fields present in the request are changed."""

    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    owner_customer_id: uuid.UUID | None = None
    stable_customer_id: uuid.UUID | None = None  # null clears it
    birth_year: BirthYear | None = None  # null clears it
    sex: HorseSex | None = None  # null clears it
    breed: Breed | None = None  # null clears it
    active: bool | None = None

    _check_year = field_validator("birth_year")(_not_in_the_future)

    @field_validator("name", "owner_customer_id", "active")
    @classmethod
    def not_null(cls, value):
        # Runs only for fields that were sent; these are required.
        if value is None:
            raise ValueError("may not be null")
        return value


class CustomerRef(BaseModel):
    """Compact read-only view of a referenced customer."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    active: bool  # lets a UI flag a horse whose owner/stable was deactivated


class HorseRead(BaseModel):
    id: uuid.UUID
    name: str
    owner_customer_id: uuid.UUID
    stable_customer_id: uuid.UUID | None
    owner: CustomerRef
    stable: CustomerRef | None
    birth_year: int | None
    sex: HorseSex | None
    breed: str | None
    active: bool
    created_at: datetime
    updated_at: datetime
