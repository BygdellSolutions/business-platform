"""A person's rights over their own ACCOUNT data: a copy of it, and (by the operator) its erasure.

This is the platform's own data about a login identity: the profile, memberships, sessions, security events and the state of the
second factor. The business records an organization keeps (its customers, horses, invoices, journals) belong to that
organization, which is their controller: they are not in a person's account copy and are not touched by erasing the account.

Erasure ANONYMISES instead of deleting the row: the account's id is referenced by history written in organizations ("who
changed this", "who issued this invoice"), and that history must keep its shape. After erasure the account has no email, no name,
no credential, no session and no second factor, cannot sign in, and shows as "Erased account" wherever the history names it.
"""

import uuid
from datetime import datetime

from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from app.core import mfa_service, security_events
from app.models import Organization, OrganizationUser, User
from app.models.auth import AuthSession, MfaChallenge, SecurityEvent, UserCredential, UserMfa, UserRecoveryCode, UserSetupToken

ERASED_NAME = "Erased account"
EVENT_LIMIT = 1000  # the newest security events a copy carries (they are kept for 30 days anyway)


class ErasureRefused(Exception):
    """The account cannot be erased yet; the message says what to do first (safe to show the operator)."""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def export_account(db: Session, user: User) -> dict:
    """Everything the platform holds about this account, as plain data. Never a password hash, a token or a second-factor key."""
    memberships = db.execute(
        select(Organization.id, Organization.name, OrganizationUser.role, OrganizationUser.created_at)
        .join(OrganizationUser, OrganizationUser.organization_id == Organization.id)
        .where(OrganizationUser.user_id == user.id)
        .order_by(Organization.name, Organization.id)
    ).all()
    sessions = db.scalars(select(AuthSession).where(AuthSession.user_id == user.id).order_by(AuthSession.created_at.desc()).limit(200)).all()
    events = db.scalars(select(SecurityEvent).where(SecurityEvent.actor_user_id == user.id).order_by(SecurityEvent.occurred_at.desc(), SecurityEvent.id.desc()).limit(EVENT_LIMIT)).all()
    mfa_row = db.get(UserMfa, user.id)
    credential = db.get(UserCredential, user.id)
    return {
        "about": "A copy of the data the platform holds about your account. Business records that organizations keep (customers, invoices, notes) belong to those organizations.",
        "profile": {
            "id": str(user.id),
            "email": user.email,
            "name": user.name,
            "active": user.is_active,
            "created_at": _iso(user.created_at),
            "can_create_organizations": user.max_owned_organizations,
            "password_set": credential is not None,
            "password_changed_at": _iso(credential.password_changed_at) if credential is not None else None,
        },
        "memberships": [{"organization_id": str(o), "organization_name": n, "role": r, "since": _iso(s)} for o, n, r, s in memberships],
        "second_factor": {
            "enabled": mfa_row is not None and mfa_row.enabled_at is not None,
            "enabled_at": _iso(mfa_row.enabled_at) if mfa_row is not None else None,
            "recovery_codes_left": mfa_service.recovery_codes_left(db, user.id),
        },
        "sessions": [
            {"created_at": _iso(s.created_at), "last_used_at": _iso(s.last_used_at), "expires_at": _iso(s.absolute_expires_at), "ended_at": _iso(s.revoked_at), "ended_because": s.revoked_reason, "browser": s.user_agent, "source": s.source}
            for s in sessions
        ],
        "security_events": [{"at": _iso(e.occurred_at), "type": e.event_type, "source": e.source, "detail": e.detail} for e in events],
    }


def erase_account(db: Session, user: User, now: datetime) -> None:
    """Anonymise an account and remove every credential and session it had. Refused while it still belongs to an organization."""
    locked = db.scalar(select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True))
    if locked is None:
        raise ErasureRefused("The account does not exist.")
    organizations = db.scalar(select(text("count(*)")).select_from(OrganizationUser).where(OrganizationUser.user_id == locked.id))
    if organizations:
        raise ErasureRefused(
            f"The account is still a member of {organizations} organization(s). Remove it from them first (an owner removes a member, "
            "or the person leaves); an organization keeps its own records."
        )
    old_email = locked.email
    placeholder = f"erased-{uuid.uuid4().hex[:12]}@erased.invalid"
    for model in (MfaChallenge, UserRecoveryCode, UserMfa, UserCredential, AuthSession, UserSetupToken):
        db.execute(delete(model).where(model.user_id == locked.id))
    # Invitations addressed to this email keep their place in the organization's history but no longer name the person.
    db.execute(text("UPDATE organization_invitations SET email = :new WHERE email = :old"), {"new": placeholder, "old": old_email})
    db.execute(update(User).where(User.id == locked.id).values(email=placeholder, name=ERASED_NAME, is_active=False, max_owned_organizations=0))
    security_events.record(db, "account_erased", now, actor_user_id=locked.id)
    db.commit()
