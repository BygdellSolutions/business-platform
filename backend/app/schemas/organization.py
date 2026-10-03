import uuid
from datetime import datetime
from typing import Annotated

from pydantic import StringConstraints, field_validator

from app.schemas.profile import CurrencyCode, ProfileIn, ProfileRead, optional_text

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
LegalName = optional_text(255)

# There is no organization_id field anywhere: the organization is always the active one.


class OrganizationUpdate(ProfileIn):
    """Partial update of the active organization's settings: only fields present are changed.

    `default_currency` can be set or changed but never cleared (null is refused): an organization
    that has a currency keeps one. Changing a currency that is already set is refused by the
    endpoint once items or transactions exist.
    """

    name: Name | None = None
    legal_name: LegalName = None
    default_currency: CurrencyCode | None = None

    @field_validator("name", "default_currency")
    @classmethod
    def not_null(cls, value):
        # Runs only for fields that were sent.
        if value is None:
            raise ValueError("may not be null")
        return value


class OrganizationRead(ProfileRead):
    id: uuid.UUID
    name: str
    legal_name: str | None
    default_currency: str | None
    # Whether the currency can still be changed, and if not, why (shown next to the field).
    default_currency_locked: bool
    default_currency_lock_reason: str | None
    created_at: datetime
    updated_at: datetime
