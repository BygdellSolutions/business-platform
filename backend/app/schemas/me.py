import uuid

from pydantic import BaseModel

from app.models import Role


class MeUser(BaseModel):
    id: uuid.UUID
    email: str
    name: str


class MeOrganization(BaseModel):
    id: uuid.UUID
    name: str


class MeResponse(BaseModel):
    user: MeUser
    organization: MeOrganization
    role: Role
