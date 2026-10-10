import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.models import Role


class MemberRead(BaseModel):
    """What membership administration needs and nothing more: no user id, credential or session state, no
    capability flag, and nothing about the user's other organizations."""

    id: uuid.UUID  # the MEMBERSHIP id (the handle for change and removal; meaningful only inside this organization)
    name: str
    email: str
    role: Role
    is_you: bool


class RoleChange(BaseModel):
    """The only thing a role change carries is the desired role: the organization is the URL's, the target is the
    path's membership id, and the actor and their role are resolved on the server."""

    model_config = ConfigDict(extra="forbid")

    role: Role


class RecentAuthentication(BaseModel):
    """Proof of recent authentication for a destructive action: today the account's password. (The development
    identity has none and is exempt; the field may then be left out.)"""

    model_config = ConfigDict(extra="forbid")

    password: str | None = Field(default=None, max_length=1024)


class LeaveRequest(RecentAuthentication):
    pass


class OwnershipTransfer(RecentAuthentication):
    membership_id: uuid.UUID


class OrganizationDeletion(RecentAuthentication):
    # The organization's name, typed by the person as the last confirmation.
    confirm_name: str = Field(max_length=255)


class Colleague(BaseModel):
    """A member as any other member may see them: who can be named as having performed a service. No email, role or
    membership id."""

    user_id: uuid.UUID
    name: str
