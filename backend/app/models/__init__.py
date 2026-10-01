# Import every model here so Alembic autogenerate sees it on Base.metadata.
from app.models.customer import Customer, CustomerType
from app.models.item import Item, ItemType
from app.models.organization import Organization
from app.models.organization_user import OrganizationUser, Role
from app.models.user import User

__all__ = [
    "Customer",
    "CustomerType",
    "Item",
    "ItemType",
    "Organization",
    "OrganizationUser",
    "Role",
    "User",
]
