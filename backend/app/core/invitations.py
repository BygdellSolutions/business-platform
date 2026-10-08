"""Organization invitations: an owner/admin invites a person; the person accepts and an ordinary membership appears.

The invitation token is NOT authentication, NOT tenant selection and NOT authority to choose a role. The token
only locates ONE invitation row; the organization and the role come from that locked row, and acceptance as an
existing account additionally requires the authenticated user's email to equal the invitation's email.

Authority and the combined lock order (compatible with membership administration, `app.core.memberships`)
--------------------------------------------------------------------------------------------------------
    1. the organization's membership rows   SELECT ... WHERE organization_id = :org ORDER BY id FOR UPDATE   (S4's lock)
    2. the invitation row                   SELECT ... WHERE id = :id FOR UPDATE   (or the pending row for org+email)
    3. the accepting user's row             (acceptance only, to re-check they are still active)

Create, revoke and regenerate take 1 then 2 (the actor's authority is re-read from the locked membership rows,
never from the tenant context captured earlier). Acceptance starts from the token: it reads the invitation
WITHOUT a lock only to learn the organization, then takes 1, 2, 3 in the same order and re-checks everything. Every
operation that takes more than one of these takes them in this order, so none can deadlock with another or with
membership administration (which takes only 1). Acceptance never changes an existing membership: it only INSERTS one.

Lifecycle: pending -> accepted | revoked. An expired pending invitation is not usable and keeps its "one pending per
organization and email" slot until it is superseded (revoked) by a new invitation for the same email. Revoking
never touches a membership; an accepted invitation cannot be revoked.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import auth_service, memberships, ownership, sessions
from app.core.config import settings
from app.core.security_events import normalize_email
from app.core.tokens import hash_token, looks_like_token, new_token
from app.models import Organization, OrganizationInvitation, OrganizationUser, Role, SecurityEvent, User
from app.models.auth import UserCredential


class InvitationNotFound(Exception):
    """No usable invitation for this token or id (random, revoked, expired and used all look the same)."""


class WrongAccount(Exception):
    """The authenticated account is not the one the invitation was made for. Nothing is consumed."""


class AlreadyMember(Exception):
    """An invitation cannot be made for someone who already belongs to the organization."""


class PendingExists(Exception):
    """A pending, unexpired invitation for this email already exists (regenerate it to replace it)."""


class InvitationAccepted(Exception):
    """The invitation was already accepted: it cannot be revoked or regenerated."""


class InvitationNotPending(Exception):
    """The invitation was revoked already: it cannot be regenerated."""


class AccountExists(Exception):
    """The invited email already has an account: the invitee signs in and accepts as that account."""


@dataclass(frozen=True)
class Created:
    invitation: OrganizationInvitation
    token: str  # the raw secret: exists only in this return value


@dataclass(frozen=True)
class Accepted:
    organization_id: uuid.UUID
    role: str  # the role the membership actually has (an existing membership keeps its own)
    joined: bool  # False when the person was already a member and nothing was changed
    replayed: bool = False


@dataclass(frozen=True)
class Preview:
    organization_name: str
    email: str
    role: str
    account_exists: bool


def _event(db: Session, event_type: str, now: datetime, *, actor_user_id, organization_id, detail: str, source: str | None) -> None:
    # Ids and roles only: never a token, a token hash, an email, a name or a password.
    db.add(SecurityEvent(occurred_at=now, event_type=event_type, actor_user_id=actor_user_id, organization_id=organization_id, source=source, detail=detail))


def state_of(invitation: OrganizationInvitation, now: datetime) -> str:
    if invitation.accepted_at is not None:
        return "accepted"
    if invitation.revoked_at is not None:
        return "revoked"
    return "expired" if invitation.expires_at <= now else "pending"


def _authorize(rows, actor_user_id, role: str) -> OrganizationUser:
    """The actor (fresh, locked) must be an owner/admin; an admin may only deal with accountant/employee/viewer."""
    actor = memberships.administrator(rows, actor_user_id)
    if Role(actor.role) == Role.ADMIN and Role(role) not in memberships.ADMIN_MANAGEABLE:
        raise memberships.NotAllowed("insufficient_authority", "An admin can only invite or manage accountants, employees and viewers.")
    return actor


def _is_member(db: Session, organization_id: uuid.UUID, email: str) -> bool:
    return db.scalar(
        select(OrganizationUser.id)
        .join(User, User.id == OrganizationUser.user_id)
        .where(OrganizationUser.organization_id == organization_id, User.email == email)
    ) is not None


def _lock_invitation(db: Session, invitation_id: uuid.UUID, organization_id: uuid.UUID) -> OrganizationInvitation | None:
    return db.scalar(
        select(OrganizationInvitation)
        .where(OrganizationInvitation.id == invitation_id, OrganizationInvitation.organization_id == organization_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _new_invitation(db: Session, *, organization_id, email: str, role: str, created_by, now: datetime) -> Created:
    token = new_token()
    invitation = OrganizationInvitation(
        organization_id=organization_id,
        email=email,
        role=role,
        token_hash=hash_token(token),
        expires_at=now + timedelta(days=settings.invitation_ttl_days),
        created_by=created_by,
    )
    db.add(invitation)
    db.flush()
    return Created(invitation, token)


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if "uq_organization_invitations_pending" in str(error.orig):
            raise PendingExists() from error
        raise


# --- administration (tenant-scoped) -----------------------------------------------------------------------------------------------------


def create(db: Session, *, organization_id: uuid.UUID, actor_user_id: uuid.UUID, email: str, role: Role, now: datetime, source: str | None) -> Created:
    email = normalize_email(email)
    rows = memberships.lock_members(db, organization_id)
    actor = _authorize(rows, actor_user_id, role)
    if _is_member(db, organization_id, email):
        raise AlreadyMember()
    pending = db.scalar(
        select(OrganizationInvitation)
        .where(OrganizationInvitation.organization_id == organization_id, OrganizationInvitation.email == email, OrganizationInvitation.revoked_at.is_(None), OrganizationInvitation.accepted_at.is_(None))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if pending is not None:
        if pending.expires_at > now:
            raise PendingExists()
        pending.revoked_at = now  # an expired invitation is superseded explicitly: its slot is freed by revoking it
        db.flush()
        _event(db, "invitation_revoked", now, actor_user_id=actor.user_id, organization_id=organization_id, detail=f"{pending.id} superseded", source=source)
    created = _new_invitation(db, organization_id=organization_id, email=email, role=str(role), created_by=actor.user_id, now=now)
    _event(db, "invitation_created", now, actor_user_id=actor.user_id, organization_id=organization_id, detail=f"{created.invitation.id} {role}", source=source)
    _commit(db)
    return created


def list_pending(db: Session, organization_id: uuid.UUID) -> list[OrganizationInvitation]:
    return list(
        db.scalars(
            select(OrganizationInvitation)
            .where(OrganizationInvitation.organization_id == organization_id, OrganizationInvitation.revoked_at.is_(None), OrganizationInvitation.accepted_at.is_(None))
            .order_by(OrganizationInvitation.created_at, OrganizationInvitation.id)
        )
    )


def revoke(db: Session, *, organization_id: uuid.UUID, actor_user_id: uuid.UUID, invitation_id: uuid.UUID, now: datetime, source: str | None) -> None:
    rows = memberships.lock_members(db, organization_id)
    actor = memberships.administrator(rows, actor_user_id)
    invitation = _lock_invitation(db, invitation_id, organization_id)
    if invitation is None:
        raise InvitationNotFound()
    _authorize(rows, actor_user_id, invitation.role)
    if invitation.accepted_at is not None:
        raise InvitationAccepted()
    if invitation.revoked_at is None:  # revoking a revoked invitation is a harmless no-op
        invitation.revoked_at = now
        db.flush()
        _event(db, "invitation_revoked", now, actor_user_id=actor.user_id, organization_id=organization_id, detail=f"{invitation.id} {invitation.role}", source=source)
    _commit(db)


def regenerate(db: Session, *, organization_id: uuid.UUID, actor_user_id: uuid.UUID, invitation_id: uuid.UUID, now: datetime, source: str | None) -> Created:
    """Revoke the invitation and issue a NEW one (new random token) for the same email and role, in one transaction."""
    rows = memberships.lock_members(db, organization_id)
    actor = memberships.administrator(rows, actor_user_id)
    old = _lock_invitation(db, invitation_id, organization_id)
    if old is None:
        raise InvitationNotFound()
    _authorize(rows, actor_user_id, old.role)
    if old.accepted_at is not None:
        raise InvitationAccepted()
    if old.revoked_at is not None:
        raise InvitationNotPending()
    if _is_member(db, organization_id, old.email):
        raise AlreadyMember()
    old.revoked_at = now
    db.flush()
    _event(db, "invitation_revoked", now, actor_user_id=actor.user_id, organization_id=organization_id, detail=f"{old.id} regenerated", source=source)
    created = _new_invitation(db, organization_id=organization_id, email=old.email, role=old.role, created_by=actor.user_id, now=now)
    _event(db, "invitation_created", now, actor_user_id=actor.user_id, organization_id=organization_id, detail=f"{created.invitation.id} {old.role}", source=source)
    _commit(db)
    return created


# --- the invitee (not organization-scoped: the token locates the invitation) -----------------------------------------------------------


def _find(db: Session, token: str) -> OrganizationInvitation | None:
    """Locate an invitation by its token WITHOUT locking (only to learn the organization, or to preview)."""
    if not looks_like_token(token):
        return None
    return db.scalar(select(OrganizationInvitation).where(OrganizationInvitation.token_hash == hash_token(token)))


def _usable(invitation: OrganizationInvitation | None, now: datetime) -> bool:
    return invitation is not None and invitation.accepted_at is None and invitation.revoked_at is None and invitation.expires_at > now


def preview(db: Session, token: str, now: datetime) -> Preview:
    invitation = _find(db, token)
    if not _usable(invitation, now):
        raise InvitationNotFound()
    organization = db.get(Organization, invitation.organization_id)
    account = db.scalar(select(User.id).where(User.email == invitation.email))
    return Preview(organization.name, invitation.email, invitation.role, account is not None)


def usable_email(db: Session, token: str, now: datetime) -> str:
    """The invited email of a usable invitation (for the password policy check before any hashing)."""
    invitation = _find(db, token)
    if not _usable(invitation, now):
        raise InvitationNotFound()
    return invitation.email


def _locked(db: Session, token: str) -> tuple[list[OrganizationUser], OrganizationInvitation]:
    """Lock order 1 then 2, from the token: unlocked read for the organization, the membership rows, then the invitation."""
    first = _find(db, token)
    if first is None:
        raise InvitationNotFound()
    rows = memberships.lock_members(db, first.organization_id)
    invitation = _lock_invitation(db, first.id, first.organization_id)
    if invitation is None or invitation.token_hash != hash_token(token):
        raise InvitationNotFound()
    return rows, invitation


def accept_existing(db: Session, *, user: User, token: str, now: datetime, source: str | None) -> Accepted:
    """An authenticated account accepts. The bearer token alone is not enough: the account's email must be the invited one."""
    _, invitation = _locked(db, token)
    current = auth_service.lock_user(db, user.id)  # lock order 3: still active?
    if current is None or not current.is_active:
        raise InvitationNotFound()
    # A FRESH statement, not the locked set: a row inserted by an acceptance that just committed is not in a snapshot
    # taken before it (READ COMMITTED), and "am I a member?" must be answered from the committed state now.
    member = db.scalar(
        select(OrganizationUser)
        .where(OrganizationUser.organization_id == invitation.organization_id, OrganizationUser.user_id == user.id)
        .execution_options(populate_existing=True)
    )

    if invitation.accepted_at is not None:
        if invitation.accepted_by == user.id and member is not None:
            return Accepted(invitation.organization_id, member.role, joined=False, replayed=True)  # an idempotent retry by the same person
        raise InvitationNotFound()
    if invitation.revoked_at is not None or invitation.expires_at <= now:
        raise InvitationNotFound()
    if invitation.email != normalize_email(user.email):
        raise WrongAccount()  # nothing is consumed

    joined = False  # an existing membership is NEVER changed by an invitation: its role stays whatever it is
    if member is None and invitation.role == Role.OWNER:
        # Becoming an owner counts against the account's limit. Refused WHOLE: no other role instead, nothing
        # consumed; the invitation stays pending (until a slot frees, the limit grows, or it is changed/revoked).
        ownership.ensure_can_own_another(db, user.id)
    if member is None:
        try:
            with db.begin_nested():
                member = OrganizationUser(organization_id=invitation.organization_id, user_id=user.id, role=invitation.role)
                db.add(member)
                db.flush()
            joined = True
        except IntegrityError:
            # Another path created the membership after the rows were locked (inserts are not blocked by row locks):
            # the person IS a member now, so settle the invitation and keep whatever role that membership has.
            member = db.scalar(
                select(OrganizationUser)
                .where(OrganizationUser.organization_id == invitation.organization_id, OrganizationUser.user_id == user.id)
                .execution_options(populate_existing=True)
            )
    invitation.accepted_at, invitation.accepted_by = now, user.id
    db.flush()
    _event(db, "invitation_accepted", now, actor_user_id=user.id, organization_id=invitation.organization_id, detail=f"{invitation.id} {invitation.role if joined else 'existing'}", source=source)
    _commit(db)
    return Accepted(invitation.organization_id, member.role, joined=joined)


def accept_new(
    db: Session, *, token: str, name: str, password_hash: str, now: datetime, source: str | None, user_agent: str | None
) -> tuple[auth_service.SessionIssued, Accepted]:
    """Create the account the invitation was made for: User, credential, membership, session and the settled
    invitation in ONE transaction. The email comes from the invitation; the password is already hashed (outside
    the transaction: nothing is written until every check has passed)."""
    rows, invitation = _locked(db, token)
    if not _usable(invitation, now):
        raise InvitationNotFound()
    if db.scalar(select(User.id).where(User.email == invitation.email)) is not None:
        raise AccountExists()
    try:
        with db.begin_nested():  # a savepoint: losing the race for the email must not poison the transaction
            user = User(email=invitation.email, name=name)
            db.add(user)
            db.flush()
    except IntegrityError as error:
        raise AccountExists() from error
    db.add(UserCredential(user_id=user.id, password_hash=password_hash, password_changed_at=now))
    db.add(OrganizationUser(organization_id=invitation.organization_id, user_id=user.id, role=invitation.role))
    invitation.accepted_at, invitation.accepted_by = now, user.id
    db.flush()
    session, session_token, csrf = sessions.create_session(db, user, now, user_agent=user_agent, source=source)
    _event(db, "invitation_accepted", now, actor_user_id=user.id, organization_id=invitation.organization_id, detail=f"{invitation.id} {invitation.role}", source=source)
    _commit(db)
    issued = auth_service.SessionIssued(token=session_token, csrf_token=csrf, expires_at=session.absolute_expires_at, user=user)
    return issued, Accepted(invitation.organization_id, invitation.role, joined=True)

