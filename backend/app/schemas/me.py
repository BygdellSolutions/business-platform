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
    # Computed: whether this account has created fewer organizations than it may ("Created 1 / 1" cannot create).
    can_create_organizations: bool
    created_organizations: int
    max_owned_organizations: int  # how many it may create (historical name; app.core.ownership)
    mfa_enabled: bool = False
    mfa_required: bool = False  # an owner or admin, with the policy on: the application sends them to set it up first


class MeOrganization(BaseModel):
    id: uuid.UUID
    name: str


class MeResponse(BaseModel):
    user: MeUser
    organization: MeOrganization
    role: Role
