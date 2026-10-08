import uuid

from pydantic import BaseModel

from app.models import Role


class MyOrganization(BaseModel):
    """One of the current user's own memberships (no tenant context is involved)."""

    id: uuid.UUID
    name: str
    role: Role


class MeUser(BaseModel):
    id: uuid.UUID
    email: str
    name: str


class MyUser(BaseModel):
    """The authenticated user themselves (no organization involved): what a client may show as "signed in as"."""

    id: uuid.UUID
    email: str
    name: str
    # Computed: whether this account owns fewer organizations than it may ("Owned 1 / 1" cannot create).
    can_create_organizations: bool
    owned_organizations: int
    max_owned_organizations: int


class MeOrganization(BaseModel):
    id: uuid.UUID
    name: str


class MeResponse(BaseModel):
    user: MeUser
    organization: MeOrganization
    role: Role
