import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from app.models import CustomerType
from app.schemas.money import DiscountPercentIn, PercentOut
from app.schemas.profile import ProfileIn, ProfileRead

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Email = Annotated[str, StringConstraints(strip_whitespace=True, max_length=320)]
Phone = Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)]

# Neither input schema has an organization_id field, and extra="forbid" turns a
# client-supplied one into a 422. The organization always comes from the tenant context.


class CustomerCreate(ProfileIn):
    customer_type: CustomerType
    name: Name
    email: Email | None = None
    phone: Phone | None = None
    active: bool = True
    # Owners and admins only (checked by the endpoint). Null: no permanent discount.
    default_discount_percent: DiscountPercentIn | None = None


class CustomerUpdate(ProfileIn):
    """Partial update: only fields present in the request are changed. A profile field is
    cleared by sending null or blank text."""

    customer_type: CustomerType | None = None
    name: Name | None = None
    email: Email | None = None
    phone: Phone | None = None
    active: bool | None = None
    default_discount_percent: DiscountPercentIn | None = None  # null removes it; owners and admins only

    @field_validator("customer_type", "name", "active")
    @classmethod
    def not_null(cls, value):
        # Runs only for fields that were sent; these columns are NOT NULL.
        if value is None:
            raise ValueError("may not be null")
        return value


class CustomerRef(BaseModel):
    """Compact read-only view of a customer referenced by another record."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    active: bool  # lets a UI flag a record whose customer was deactivated


class CustomerRead(ProfileRead):
    id: uuid.UUID
    number: int  # per organization, from the database (app.models.mixins.Numbered)
    customer_type: CustomerType
    name: str
    email: str | None
    phone: str | None
    active: bool
    default_discount_percent: PercentOut | None
    created_at: datetime
    updated_at: datetime
    # Who created it and who changed it last (null: not recorded, e.g. before authors were kept).
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None
