"""How many organizations an account may OWN (decided 2026-10-08).

`users.max_owned_organizations` is an account entitlement (default 1: "Owned 1 / 1"); later plans can raise it. It
counts owner-role memberships only; being a member of any number of organizations costs nothing. EVERY way of
gaining ownership checks it here, under the receiving user's row lock: creating an organization, an ownership
transfer, a promotion to owner and accepting an owner invitation. A database trigger repeats the check (taking the
same lock) so a future path that forgets this function is still refused. The operator's `repair owner` is the one
administrative override (a transaction-local setting the trigger honours).
"""

import uuid

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models import OrganizationUser, Role, User

LIMIT_CONSTRAINT = "owned_organization_limit"  # named in the trigger's error, mapped back to OwnershipLimitReached


class OwnershipLimitReached(Exception):
    """The account already owns as many organizations as it may."""


def owned_count(db: Session, user_id: uuid.UUID) -> int:
    return db.scalar(
        select(func.count()).select_from(OrganizationUser).where(OrganizationUser.user_id == user_id, OrganizationUser.role == Role.OWNER)
    )


def ensure_can_own_another(db: Session, user_id: uuid.UUID) -> None:
    """Lock the account's row (fresh read of its entitlement) and refuse if one more owned organization would exceed it."""
    limit = db.scalar(select(User.max_owned_organizations).where(User.id == user_id).with_for_update())
    if limit is None or owned_count(db, user_id) >= limit:
        raise OwnershipLimitReached()


def allow_beyond_limit_in_this_transaction(db: Session) -> None:
    """Operator override (repair owner only): the trigger lets owner memberships through for this transaction."""
    db.execute(text("SELECT set_config('app.owner_limit_override', 'on', true)"))


def is_limit_violation(error: Exception) -> bool:
    return LIMIT_CONSTRAINT in str(getattr(error, "orig", error))
