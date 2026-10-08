"""The copies an invoice keeps of the parties and of the custom-field context.

Only fields that exist today are copied; nothing is invented for later. The structures are
versioned (`schema`) so a reader can tell which fields to expect from an old invoice.
"""

from collections.abc import Sequence
from typing import Any

from app.models import Customer, Organization
from app.modules.custom_fields.schemas import ValueRead
from app.modules.invoicing.models import ISSUER_SNAPSHOT_SCHEMA, SNAPSHOT_SCHEMA

PROFILE_FIELDS = (
    "address_line1",
    "address_line2",
    "postal_code",
    "city",
    "country_code",
    "registration_number",
    "vat_number",
)
# What schema 2 of the issuer snapshot adds: how to reach and pay the seller, and the documents' language.
ISSUER_FIELDS = (
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
    }


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
