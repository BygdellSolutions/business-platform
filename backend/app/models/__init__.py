# Import every model here so Alembic autogenerate sees it on Base.metadata.
from app.models.audit import AuditEvent
from app.models.auth import AuthSession, SecurityEvent, UserCredential, UserSetupToken
from app.models.customer import Customer, CustomerType
from app.models.item import Item, ItemDiscount, ItemType
from app.models.invitation import OrganizationInvitation
from app.models.organization import Organization
from app.models.organization_request import OrganizationCreationRequest
from app.models.organization_user import OrganizationUser, Role
from app.models.record_counter import RecordCounter
from app.models.supplier import Supplier
from app.models.user import User

__all__ = [
    "AuditEvent",
    "AuthSession",
    "SecurityEvent",
    "UserCredential",
    "UserSetupToken",
    "Customer",
    "CustomerType",
    "Item",
    "ItemDiscount",
    "ItemType",
    "Organization",
    "OrganizationInvitation",
    "OrganizationCreationRequest",
    "OrganizationUser",
    "RecordCounter",
    "Role",
    "Supplier",
    "User",
]
