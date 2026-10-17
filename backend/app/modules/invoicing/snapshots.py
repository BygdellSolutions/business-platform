"""The copies an invoice keeps of the parties and of the custom-field context.

Only fields that exist today are copied; nothing is invented for later. The structures are
versioned (`schema`) so a reader can tell which fields to expect from an old invoice.
"""

import hashlib
import logging
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer, Organization, OrganizationLogo
from app.modules.custom_fields.schemas import ValueRead
from app.modules.invoicing.models import ISSUER_SNAPSHOT_SCHEMA, SNAPSHOT_SCHEMA

logger = logging.getLogger("app.invoicing.snapshots")

PROFILE_FIELDS = (
    "address_line1",
    "address_line2",
    "postal_code",
    "city",
    "country_code",
    "registration_number",
    "vat_number",
)
# What schema 2 of the issuer snapshot adds (schema 4 adds the registered office): how to reach and pay the seller, and the documents' language.
ISSUER_FIELDS = (
    "registered_office",
    "phone",
    "email",
    "website",
    "bankgiro",
    "plusgiro",
    "iban",
    "bic",
    "payment_terms_days",
    "approved_for_f_tax",
    "document_language",
)


def customer_snapshot(customer: Customer) -> dict[str, Any]:
    """The buyer, as it is now."""
    return {
        "schema": SNAPSHOT_SCHEMA,
        "customer_id": str(customer.id),
        "customer_type": str(getattr(customer.customer_type, "value", customer.customer_type)),
        "name": customer.name,
        "email": customer.email,
        "phone": customer.phone,
        **{field: getattr(customer, field) for field in PROFILE_FIELDS},
    }


def issuer_snapshot(organization: Organization, our_reference: str | None = None) -> dict[str, Any]:
    """The seller, as it is now (the organization's own profile; its default currency is not
    part of it: the invoice carries the currency of its sources). Schema 2: also contact, payment
    details, F-tax and the document language, so an issued invoice never depends on today's settings."""
    return {
        "schema": ISSUER_SNAPSHOT_SCHEMA,
        "organization_id": str(organization.id),
        "name": organization.name,
        "legal_name": organization.legal_name,
        **{field: getattr(organization, field) for field in PROFILE_FIELDS},
        **{field: getattr(organization, field) for field in ISSUER_FIELDS},
        # Schema 3: the person at the seller the customer can turn to ("Vår referens"): who issued the invoice (who
        # created the draft until then), by name as it was at that moment.
        "our_reference": our_reference,
        # Schema 5: the logo version in use at that moment (None: no logo), so the invoice keeps printing that picture.
        "logo_id": str(organization.logo_id) if organization.logo_id else None,
        "logo_sha256": organization.logo.sha256 if organization.logo is not None else None,
    }


def logo_bytes(db: Session, organization_id: uuid.UUID, issuer: dict[str, Any]) -> bytes | None:
    """The PNG an issuer snapshot recorded: looked up in the document's OWN organization and checked against the recorded hash.
    A missing or different picture prints no logo (and is logged) rather than another picture."""
    logo_id = issuer.get("logo_id")
    if not logo_id:
        return None
    row = db.scalar(select(OrganizationLogo).where(OrganizationLogo.organization_id == organization_id, OrganizationLogo.id == uuid.UUID(logo_id)))
    if row is None or row.sha256 != issuer.get("logo_sha256") or hashlib.sha256(row.content).hexdigest() != row.sha256:
        logger.warning("logo %s recorded on a document of organization %s is missing or changed; printing none", logo_id, organization_id)
        return None
    return row.content


def custom_field_snapshot(values: Sequence[ValueRead]) -> list[dict[str, Any]]:
    """Generic copy of the custom-field values flagged for invoices, in the order given.

    Knows nothing about what a field means. `display` is the text as it resolved at that moment
    (an option's label, a referenced record's name), so the invoice never has to look it up
    again; `missing` records a reference whose target was gone. `definition_id` is for audit
    only and is never resolved when an invoice is shown.
    """
    return [
        {
            "key": value.key,
            "label": value.label,
            "field_type": str(getattr(value.field_type, "value", value.field_type)),
            "value": value.value,
            "display": value.display,
            "missing": value.missing,
            "position": value.position,
            "definition_id": str(value.definition_id),
        }
        for value in values
    ]
