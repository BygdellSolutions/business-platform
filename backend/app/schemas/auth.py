from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Bounds only keep requests small; a malformed email is simply a failed login, like any other wrong credential.
    email: str = Field(max_length=320)
    password: str = Field(max_length=4096)


class SetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(max_length=256)
    password: str = Field(max_length=4096)
    # Required only while the account has no name (created by the operator without one); otherwise it renames.
    name: str | None = Field(default=None, max_length=255)

    @field_validator("name")
    @classmethod
    def trimmed(cls, value: str | None) -> str | None:
        return (value or "").strip() or None


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(max_length=4096)
    new_password: str = Field(max_length=4096)


class AuthUser(BaseModel):
    id: str
    email: str
    name: str
    can_create_organizations: bool


class SessionResponse(BaseModel):
    """Returned once, to the BFF: the session token and its CSRF token exist nowhere else in plain text."""

    token: str
    csrf_token: str
    expires_at: datetime
    user: AuthUser
