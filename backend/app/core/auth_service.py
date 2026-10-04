"""Authentication operations: login, password change, setup-link redemption and the operator functions.

Authentication answers "who is this user?" and nothing else. What a user may do in an organization is decided
by `organization_users` (see `app.core.tenant`); nothing here reads or writes a membership.

Login algorithm (everything that can fail looks the same from outside: `LoginFailed`):

    1. normalize the email; compute its HMAC (`identifier_hash`)
    2. throttle (`app.core.throttle.check_login`): cheap bounded counts, NO hashing yet  -> Throttled (429)
    3. take ONE bounded admission slot for password hashing                               -> AuthBusy (503)
    4. inside the slot: load user + credential; verify the password with exactly ONE Argon2 verification
       (a dummy hash when there is no user or no credential); if it was right and the stored hash is outdated,
       compute the new hash
    5. not valid (unknown, no credential, inactive, wrong password): record a `login_failure` event (unless the
       global valve is open), commit, LoginFailed
    6. valid: lock the user row, re-check it is still active, store a rehash, revoke the session the request
       presented (fixation defense), create a fresh session, enforce the session cap, record `login_success`,
       purge a small batch of old records, commit
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core import clock, passwords, security_events, sessions, throttle
from app.core.config import settings
from app.core.passwords import AuthBusy, admission
from app.core.tokens import hash_token, looks_like_token, new_token
from app.models import User
from app.models.auth import UserCredential, UserSetupToken

MAX_LOGIN_PASSWORD_LENGTH = 1024


class LoginFailed(Exception):
    """Wrong credentials, in every way they can be wrong. Carries no detail on purpose."""


class InvalidSetupLink(Exception):
    """A setup link that is unknown, used, revoked, expired or for a user who cannot use it."""


class PasswordProblem(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class WrongCurrentPassword(Exception):
    pass


Throttled = throttle.Throttled

__all__ = ["AuthBusy", "InvalidSetupLink", "LoginFailed", "PasswordProblem", "SessionIssued", "Throttled", "WrongCurrentPassword"]


@dataclass(frozen=True)
class SessionIssued:
    token: str
    csrf_token: str
    expires_at: datetime  # the absolute end; the idle limit can end it sooner
    user: User


def lock_user(db: Session, user_id) -> User | None:
    return db.scalar(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True))


def _issue(db: Session, user: User, now: datetime, *, user_agent: str | None, source: str | None) -> SessionIssued:
    session, token, csrf = sessions.create_session(db, user, now, user_agent=user_agent, source=source)
    return SessionIssued(token=token, csrf_token=csrf, expires_at=session.absolute_expires_at, user=user)


def _housekeeping(db: Session, now: datetime) -> None:
    """A small bounded batch of expired records is removed whenever someone logs in."""
    security_events.purge_events(db, now, batch=200, max_batches=1)
    sessions.purge_records(db, now, batch=200, max_batches=1)


def login(db: Session, *, email: str, password: str, source: str, user_agent: str | None, presented_token: str | None) -> SessionIssued:
    now = clock.utcnow()
    identifier = security_events.normalize_email(email)
    ident_hash = security_events.identifier_hash(identifier)
    decision = throttle.check_login(db, now, source, ident_hash)  # 2

    user: User | None = None
    valid = False
    new_hash: str | None = None
    if len(password) <= MAX_LOGIN_PASSWORD_LENGTH:
        with admission.slot():  # 3
            row = db.execute(select(User, UserCredential).outerjoin(UserCredential, UserCredential.user_id == User.id).where(User.email == identifier)).first()
            stored = None
            if row is not None and row[1] is not None:
                user, stored = row[0], row[1].password_hash
            elif row is not None:
                user = row[0]
            correct = passwords.verify(stored, password)  # 4: exactly one Argon2 verification
            valid = correct and stored is not None and user is not None and user.is_active
            if valid and passwords.needs_rehash(stored):
                new_hash = passwords.hash_password(password)

    if valid:
        locked = lock_user(db, user.id)  # 6
        if locked is None or not locked.is_active:
            valid = False
        else:
            if new_hash is not None:
                db.execute(update(UserCredential).where(UserCredential.user_id == locked.id).values(password_hash=new_hash))
            if presented_token is not None and looks_like_token(presented_token):
                db.execute(
                    update(sessions.AuthSession)
                    .where(sessions.AuthSession.token_hash == hash_token(presented_token), sessions.AuthSession.revoked_at.is_(None))
                    .values(revoked_at=now, revoked_reason="rotated")
                )
            issued = _issue(db, locked, now, user_agent=user_agent, source=source)
            security_events.record(db, "login_success", now, actor_user_id=locked.id, source=source, ident_hash=ident_hash)
            _housekeeping(db, now)
            db.commit()
            return issued

    if decision.record_failures:  # 5
        security_events.record(db, "login_failure", now, source=source, ident_hash=ident_hash)
    db.commit()
    raise LoginFailed()


def change_password(db: Session, *, user: User, session, current_password: str, new_password: str, source: str) -> None:
    now = clock.utcnow()
    throttle.check_actor_failures(db, now, "password_change_failure", settings.password_change_max_failures, user.id)
    problem = passwords.policy_problem(new_password, user.email)
    if problem is not None:
        raise PasswordProblem(problem)
    if len(current_password) > MAX_LOGIN_PASSWORD_LENGTH:
        raise WrongCurrentPassword()

    with admission.slot():
        credential = db.get(UserCredential, user.id)
        stored = credential.password_hash if credential is not None else None
        correct = passwords.verify(stored, current_password) and stored is not None
        new_hash = passwords.hash_password(new_password) if correct else None
    if not correct:
        security_events.record(db, "password_change_failure", now, actor_user_id=user.id, source=source)
        db.commit()
        raise WrongCurrentPassword()
    if new_password == current_password:
        raise PasswordProblem("The new password must differ from the current one.")

    locked = lock_user(db, user.id)
    if locked is None or not locked.is_active:
        raise WrongCurrentPassword()
    db.execute(update(UserCredential).where(UserCredential.user_id == user.id).values(password_hash=new_hash, password_changed_at=now))
    sessions.revoke_all_for_user(db, user.id, "password_changed", now, except_session_id=session.id)
    security_events.record(db, "password_changed", now, actor_user_id=user.id, source=source)
    db.commit()


def issue_setup_token(db: Session, user: User, now: datetime, *, detail: str = "cli") -> str:
    """A new single-use set-password link token for `user` (any outstanding one is revoked). Returns the raw token ONCE."""
    db.execute(
        update(UserSetupToken)
        .where(UserSetupToken.user_id == user.id, UserSetupToken.used_at.is_(None), UserSetupToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    token = new_token()
    db.add(
        UserSetupToken(
            user_id=user.id,
            token_hash=hash_token(token),
            purpose="set_password",
            created_at=now,
            expires_at=now + timedelta(hours=settings.setup_token_ttl_hours),
        )
    )
    security_events.record(db, "setup_link_issued", now, actor_user_id=user.id, detail=detail)
    return token


def _setup_failure(db: Session, now: datetime, source: str) -> InvalidSetupLink:
    security_events.record(db, "setup_failure", now, source=source)
    db.commit()
    return InvalidSetupLink()


def redeem_setup(db: Session, *, token: str, password: str, source: str, user_agent: str | None) -> SessionIssued:
    """Use a set-password link: sets the password, ends the user's other sessions, signs the user in.

    The link is consumed by one conditional UPDATE, so two simultaneous redemptions cannot both succeed.
    Password hashing happens only for a link that is still valid when first looked at, so garbage links cost
    no Argon2 work.
    """
    now = clock.utcnow()
    throttle.check_source_failures(db, now, "setup_failure", settings.setup_source_max_failures, source)
    problem = passwords.policy_problem(password)
    if problem is not None:
        raise PasswordProblem(problem)
    if not looks_like_token(token):
        raise _setup_failure(db, now, source)
    token_hash = hash_token(token)

    def live():
        return (
            UserSetupToken.token_hash == token_hash,
            UserSetupToken.used_at.is_(None),
            UserSetupToken.revoked_at.is_(None),
            UserSetupToken.expires_at > now,
        )

    if db.scalar(select(UserSetupToken.id).where(*live())) is None:
        raise _setup_failure(db, now, source)

    with admission.slot():
        new_hash = passwords.hash_password(password)

    try:
        with db.begin_nested():  # a savepoint: a request that cannot use the link must not consume it
            user_id = db.execute(update(UserSetupToken).where(*live()).values(used_at=now).returning(UserSetupToken.user_id)).scalar()
            if user_id is None:  # lost a race for the link
                raise InvalidSetupLink()
            user = lock_user(db, user_id)
            if user is None or not user.is_active or passwords.policy_problem(password, user.email) is not None:
                raise InvalidSetupLink()
            credential = db.get(UserCredential, user.id)
            if credential is None:
                db.add(UserCredential(user_id=user.id, password_hash=new_hash, password_changed_at=now))
            else:
                credential.password_hash = new_hash
                credential.password_changed_at = now
            sessions.revoke_all_for_user(db, user.id, "setup", now)
            issued = _issue(db, user, now, user_agent=user_agent, source=source)
            security_events.record(db, "setup_redeemed", now, actor_user_id=user.id, source=source)
    except InvalidSetupLink:
        raise _setup_failure(db, now, source) from None
    db.commit()
    return issued


def set_user_active(db: Session, user: User, active: bool, now: datetime) -> int:
    """Enable or disable a user (operator action). Disabling also revokes every session. Returns revoked sessions."""
    locked = lock_user(db, user.id)
    locked.is_active = active
    revoked = 0
    if not active:
        revoked = sessions.revoke_all_for_user(db, locked.id, "disabled", now)
    security_events.record(db, "user_enabled" if active else "user_disabled", now, actor_user_id=locked.id, detail="cli")
    return revoked


def set_creation_capability(db: Session, user: User, allowed: bool, now: datetime) -> bool:
    """Grant or revoke the account-level right to create organizations (operator action).

    The same user-row lock that organization creation takes, so a revoke either waits for a creation in flight
    (which then completes under the old right) or happens first (and the creation is refused). Memberships and
    roles are never touched: this right is a property of the account, not of any membership. Returns whether
    the value changed.
    """
    locked = lock_user(db, user.id)
    changed = locked.can_create_organizations != allowed
    locked.can_create_organizations = allowed
    security_events.record(db, "capability_changed", now, actor_user_id=locked.id, detail=("org_creation_granted" if allowed else "org_creation_revoked") + (":cli" if changed else ":cli:unchanged"))
    return changed
