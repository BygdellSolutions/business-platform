import uuid

from pydantic import BaseModel, ConfigDict

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
