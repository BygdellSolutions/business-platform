import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from app.schemas.profile import ProfileIn, ProfileRead, optional_text

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
ContactPerson = optional_text(255)
Email = optional_text(320)
Phone = optional_text(64)
CustomerNumber = optional_text(64)

# As for customers: no organization_id field (extra="forbid"); the organization comes from the tenant context.


class SupplierCreate(ProfileIn):
    name: Name
    contact_person: ContactPerson = None
    email: Email = None
    phone: Phone = None
    our_customer_number: CustomerNumber = None
    active: bool = True


class SupplierUpdate(ProfileIn):
    """Partial update: only fields present in the request are changed; blank or null clears an optional field."""

    name: Name | None = None
    contact_person: ContactPerson = None
    email: Email = None
    phone: Phone = None
    our_customer_number: CustomerNumber = None
    active: bool | None = None

    @field_validator("name", "active")
    @classmethod
    def not_null(cls, value):
        # Runs only for fields that were sent; these columns are NOT NULL.
        if value is None:
            raise ValueError("may not be null")
        return value


class SupplierRef(BaseModel):
    """Compact read-only view of a supplier referenced by another record."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    active: bool  # lets a UI flag a delivery whose supplier was deactivated


class SupplierRead(ProfileRead):
    id: uuid.UUID
    number: int  # per organization, from the database (app.models.mixins.Numbered)
    name: str
    contact_person: str | None
    email: str | None
    phone: str | None
    our_customer_number: str | None
    active: bool
    created_at: datetime
    updated_at: datetime
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None
