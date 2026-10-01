import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from app.models import CustomerType

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Email = Annotated[str, StringConstraints(strip_whitespace=True, max_length=320)]
Phone = Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)]

# Neither input schema has an organization_id field, and extra="forbid" turns a
# client-supplied one into a 422. The organization always comes from the tenant context.


class CustomerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_type: CustomerType
    name: Name
    email: Email | None = None
    phone: Phone | None = None
    active: bool = True


class CustomerUpdate(BaseModel):
    """Partial update: only fields present in the request are changed."""

    model_config = ConfigDict(extra="forbid")

    customer_type: CustomerType | None = None
    name: Name | None = None
    email: Email | None = None
    phone: Phone | None = None
    active: bool | None = None

    @field_validator("customer_type", "name", "active")
    @classmethod
    def not_null(cls, value):
        # Runs only for fields that were sent; these columns are NOT NULL.
        if value is None:
            raise ValueError("may not be null")
        return value


class CustomerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_type: CustomerType
    name: str
    email: str | None
    phone: str | None
    active: bool
    created_at: datetime
    updated_at: datetime
