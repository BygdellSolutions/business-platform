"""Who must have a second factor: owners and admins (a role in ANY organization), when the policy is on.

The policy is on in production by default and off elsewhere (`MFA_REQUIRED_FOR_ADMINS` overrides either way), and it only applies
to real sign-ins (AUTH_MODE=session). It is enforced on the server: an owner or admin without a second factor is refused on every
tenant request (see `app.core.tenant`), whatever the browser shows.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import mfa_service
from app.core.config import settings
from app.models import OrganizationUser, Role, User

ADMIN_ROLES = (Role.OWNER, Role.ADMIN)


def policy_on() -> bool:
    if settings.auth_mode != "session":
        return False
    if settings.mfa_required_for_admins is not None:
        return settings.mfa_required_for_admins
    return settings.app_env == "production"


def required_for_user(db: Session, user: User) -> bool:
    """Must THIS person have a second factor (policy on and an owner or admin somewhere)."""
    if not policy_on():
        return False
    return db.scalar(select(OrganizationUser.id).where(OrganizationUser.user_id == user.id, OrganizationUser.role.in_(ADMIN_ROLES)).limit(1)) is not None


def satisfied(db: Session, user: User) -> bool:
    return not required_for_user(db, user) or mfa_service.is_enabled(db, user.id)
