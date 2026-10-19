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


class SessionInfo(BaseModel):
    """One of the caller's own signed-in browsers. Never a token, a hash or an address: only what a person can recognise."""

    id: str
    created_at: datetime
    last_used_at: datetime
    user_agent: str | None
    current: bool


class MfaRequiredResponse(BaseModel):
    """The password was right but a second factor is needed: no session yet, only a short-lived challenge."""

    mfa_required: bool = True
    challenge: str
    expires_at: datetime


class MfaLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge: str = Field(max_length=256)
    code: str = Field(max_length=64)


class MfaPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(max_length=4096)


class MfaCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(max_length=64)


class MfaStatus(BaseModel):
    enabled: bool
    required: bool  # this person must have one (an owner or admin, with the policy on)
    recovery_codes_left: int


class MfaEnrollment(BaseModel):
    secret: str  # base32, for typing into an authenticator app by hand
    uri: str  # otpauth:// link (shown as a QR code by the page)


class RecoveryCodes(BaseModel):
    recovery_codes: list[str]


class SessionsEnded(BaseModel):
    ended: int


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
