"""Optional address and business identifiers, shared by the Organization and Customer schemas.

Everything is optional free text. Blank input means "not set" (stored as NULL) so the
database never holds the two different "empty"s. The only structure checked is the SHAPE of a
country code (two capital letters) and of a currency code (three capital letters), and the
input is upper-cased first so `se` is accepted as `SE`. There is deliberately no check of
whether a code is assigned, or of what a registration or VAT number looks like: that is
jurisdiction policy, not a platform rule.
"""

from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, StringConstraints


def _blank_to_none(value):
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _code(value):
    value = _blank_to_none(value)
    return value.upper() if isinstance(value, str) else value


def optional_text(max_length: int):
    return Annotated[
        Annotated[str, StringConstraints(max_length=max_length)] | None,
        BeforeValidator(_blank_to_none),
    ]


AddressLine = optional_text(255)
PostalCode = optional_text(32)
City = optional_text(128)
RegistrationNumber = optional_text(64)
VatNumber = optional_text(64)
CountryCode = Annotated[
    Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")] | None, BeforeValidator(_code)
]
# Required wherever it is used (never blank); the shape is the only rule.
CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$"), BeforeValidator(_code)]


class ProfileIn(BaseModel):
    """The profile fields of a create or partial-update body."""

    model_config = ConfigDict(extra="forbid")

    address_line1: AddressLine = None
    address_line2: AddressLine = None
    postal_code: PostalCode = None
    city: City = None
    country_code: CountryCode = None
    registration_number: RegistrationNumber = None
    vat_number: VatNumber = None


class ProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    address_line1: str | None
    address_line2: str | None
    postal_code: str | None
    city: str | None
    country_code: str | None
    registration_number: str | None
    vat_number: str | None
