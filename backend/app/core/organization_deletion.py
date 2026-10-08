"""Delete an organization and everything it owns, for good.

Decided by the owner (2026-10-08): a hard delete. Every tenant-owned row goes, including issued invoices, their
frozen PDFs and the history; the confirmation the person passes through says so. Accounts stay (a user may belong
to other organizations) and so do security events, which are the platform's own log (they keep the organization id
as a plain value). The protections that normally refuse deleting issued documents and history let a row through
only while THIS transaction has set `app.deleting_organization` to that row's organization (transaction-local).

Every table with an `organization_id` must be listed here; a test compares this list with the database, so a new
tenant table cannot be forgotten.
"""

import uuid
from datetime import datetime

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.core import memberships
from app.models import Organization, SecurityEvent

# Children before parents. custom_field_definitions is handled separately (definitions refer to each other).
TABLES_IN_DELETE_ORDER = (
    "invoice_pdfs",
    "invoice_vat_rows",
    "invoice_lines",
    "invoice_transactions",
    "invoices",
    "invoice_counters",
    "custom_field_values",
    "custom_field_options",
    "custom_field_definitions",
    "transaction_lines",
    "transactions",
    "horses",
    "item_discounts",
    "items",
    "customers",
    "organization_creation_requests",
    "organization_invitations",
    "organization_users",
    "audit_events",
)
# Tables that keep their rows: the platform's security log (no foreign key to the organization).
KEPT = ("security_events",)


class ConfirmationMismatch(Exception):
    """The typed organization name does not match."""


def delete_organization(
    db: Session, *, organization_id: uuid.UUID, actor_user_id: uuid.UUID, confirm_name: str, now: datetime, source: str | None
) -> None:
    organization = db.execute(
        select(Organization).where(Organization.id == organization_id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if organization is None:
        raise memberships.NotAMember()
    memberships.lock_as_owner(db, organization_id=organization_id, actor_user_id=actor_user_id)
    if confirm_name.strip() != organization.name:
        raise ConfirmationMismatch()

    db.execute(text("SELECT set_config('app.deleting_organization', :org, true)"), {"org": str(organization_id)})
    params = {"org": organization_id}
    for table in TABLES_IN_DELETE_ORDER:
        if table == "custom_field_definitions":
            # A dependent field points at its parent (RESTRICT): remove dependents first, chain by chain.
            while db.execute(
                text(
                    "DELETE FROM custom_field_definitions d WHERE d.organization_id = :org AND NOT EXISTS ("
                    "SELECT 1 FROM custom_field_definitions c WHERE c.organization_id = :org AND c.depends_on_definition_id = d.id)"
                ),
                params,
            ).rowcount:
                pass
            continue
        db.execute(text(f"DELETE FROM {table} WHERE organization_id = :org"), params)
    db.execute(delete(Organization).where(Organization.id == organization_id))
    db.add(
        SecurityEvent(occurred_at=now, event_type="organization_deleted", actor_user_id=actor_user_id, organization_id=organization_id, source=source)
    )
    db.commit()
