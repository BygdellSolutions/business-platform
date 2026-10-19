"""The second factor: enrol, confirm, prove at sign-in, regenerate recovery codes, disable, and the operator's reset.

Every wrong code is recorded (`mfa_failure`) and counted per person, so guessing is bounded across sign-ins, enrollments and
disabling alike; a challenge also stops accepting codes after a few attempts. A TOTP code works once (the newest accepted time
step is stored under a row lock), a recovery code works once (one conditional UPDATE).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core import mfa, security_events, sessions, throttle
from app.core.config import settings
from app.core.tokens import hash_token, looks_like_token, new_token
from app.models import User
from app.models.auth import MfaChallenge, UserMfa, UserRecoveryCode


class AlreadyEnabled(Exception):
    pass


class NotEnabled(Exception):
    pass


class InvalidCode(Exception):
    """The code (or recovery code) was wrong, in any way it can be wrong. Carries no detail on purpose."""


class ChallengeInvalid(Exception):
    """The challenge is unknown, used, expired or out of attempts."""


@dataclass(frozen=True)
class Enrollment:
    secret_base32: str
    uri: str


def _row(db: Session, user_id, *, lock: bool = False) -> UserMfa | None:
    query = select(UserMfa).where(UserMfa.user_id == user_id).execution_options(populate_existing=True)
    return db.scalar(query.with_for_update() if lock else query)


def is_enabled(db: Session, user_id) -> bool:
    return db.scalar(select(UserMfa.user_id).where(UserMfa.user_id == user_id, UserMfa.enabled_at.is_not(None))) is not None


def recovery_codes_left(db: Session, user_id) -> int:
    return len(list(db.scalars(select(UserRecoveryCode.id).where(UserRecoveryCode.user_id == user_id, UserRecoveryCode.used_at.is_(None)))))


def begin_enrollment(db: Session, user: User, now: datetime) -> Enrollment:
    """Start (or restart) enrolling: a fresh seed, not yet in force. Refused while a second factor is already on."""
    row = _row(db, user.id, lock=True)
    if row is not None and row.enabled_at is not None:
        raise AlreadyEnabled()
    seed = mfa.new_seed()
    if row is None:
        db.add(UserMfa(user_id=user.id, seed=seed, created_at=now))
    else:
        row.seed, row.last_step, row.created_at = seed, None, now
    db.commit()
    secret = mfa.derive_secret(seed)
    return Enrollment(secret_base32=mfa.secret_base32(secret), uri=mfa.provisioning_uri(user.email, secret))


def _new_recovery_codes(db: Session, user_id, now: datetime) -> list[str]:
    db.execute(delete(UserRecoveryCode).where(UserRecoveryCode.user_id == user_id))
    codes = [mfa.new_recovery_code() for _ in range(mfa.RECOVERY_CODE_COUNT)]
    for code in codes:
        normalized = mfa.normalize_recovery_code(code)
        assert normalized is not None
        db.add(UserRecoveryCode(user_id=user_id, code_hash=mfa.recovery_hash(normalized), created_at=now))
    return codes


def _fail(db: Session, user_id, now: datetime, source: str | None) -> InvalidCode:
    security_events.record(db, "mfa_failure", now, actor_user_id=user_id, source=source)
    db.commit()
    return InvalidCode()


def _check_totp(db: Session, row: UserMfa, code: str, now: datetime) -> bool:
    step = mfa.verify(mfa.derive_secret(row.seed), code, now, row.last_step)
    if step is None:
        return False
    # The row is locked by the caller, so two requests cannot both pass with the same step; this also moves the replay mark forward.
    row.last_step = step
    return True


def _check_recovery(db: Session, user_id, code: str, now: datetime, source: str | None) -> bool:
    normalized = mfa.normalize_recovery_code(code)
    if normalized is None:
        return False
    used = db.execute(
        update(UserRecoveryCode)
        .where(UserRecoveryCode.user_id == user_id, UserRecoveryCode.code_hash == mfa.recovery_hash(normalized), UserRecoveryCode.used_at.is_(None))
        .values(used_at=now)
        .returning(UserRecoveryCode.id)
    ).first()
    if used is None:
        return False
    security_events.record(db, "recovery_code_used", now, actor_user_id=user_id, source=source)
    return True


def prove(db: Session, user: User, code: str, now: datetime, source: str | None) -> str:
    """Check a TOTP code or a recovery code for `user`. Returns "totp" or "recovery"; raises InvalidCode (recorded and committed) or
    throttle.Throttled. The caller commits on success."""
    throttle.check_actor_failures(db, now, "mfa_failure", settings.mfa_max_failures, user.id)
    row = _row(db, user.id, lock=True)
    if row is None or row.enabled_at is None:
        raise NotEnabled()
    if _check_totp(db, row, code, now):
        return "totp"
    if _check_recovery(db, user.id, code, now, source):
        return "recovery"
    raise _fail(db, user.id, now, source)


def confirm_enrollment(db: Session, user: User, code: str, now: datetime, source: str | None) -> list[str]:
    """The person typed a code from the app: the second factor is now in force. Returns the recovery codes, shown ONCE."""
    throttle.check_actor_failures(db, now, "mfa_failure", settings.mfa_max_failures, user.id)
    row = _row(db, user.id, lock=True)
    if row is None:
        raise NotEnabled()
    if row.enabled_at is not None:
        raise AlreadyEnabled()
    if not _check_totp(db, row, code, now):
        raise _fail(db, user.id, now, source)
    row.enabled_at = now
    codes = _new_recovery_codes(db, user.id, now)
    security_events.record(db, "mfa_enabled", now, actor_user_id=user.id, source=source)
    db.commit()
    return codes


def regenerate_recovery_codes(db: Session, user: User, code: str, now: datetime, source: str | None) -> list[str]:
    prove(db, user, code, now, source)
    codes = _new_recovery_codes(db, user.id, now)
    db.commit()
    return codes


def disable(db: Session, user: User, code: str, now: datetime, source: str | None) -> None:
    prove(db, user, code, now, source)
    db.execute(delete(UserRecoveryCode).where(UserRecoveryCode.user_id == user.id))
    db.execute(delete(UserMfa).where(UserMfa.user_id == user.id))
    security_events.record(db, "mfa_disabled", now, actor_user_id=user.id, source=source)
    db.commit()


def reset_by_operator(db: Session, user: User, now: datetime) -> None:
    """The operator's recovery for a person who lost both the app and the recovery codes: the second factor goes, every session of
    the person ends, and they enrol again after signing in with their password."""
    db.execute(delete(MfaChallenge).where(MfaChallenge.user_id == user.id))
    db.execute(delete(UserRecoveryCode).where(UserRecoveryCode.user_id == user.id))
    db.execute(delete(UserMfa).where(UserMfa.user_id == user.id))
    sessions.revoke_all_for_user(db, user.id, "operator", now)
    security_events.record(db, "mfa_reset_by_operator", now, actor_user_id=user.id)
    db.commit()


# --- the step between a correct password and a session ------------------------------------------------------------------------------------------


def create_challenge(db: Session, user: User, now: datetime, source: str | None) -> tuple[str, datetime]:
    token = new_token()
    expires = now + timedelta(seconds=settings.mfa_challenge_ttl_seconds)
    db.add(MfaChallenge(user_id=user.id, token_hash=hash_token(token), created_at=now, expires_at=expires, source=source))
    return token, expires


def complete_challenge(db: Session, token: str, code: str, now: datetime, source: str | None) -> User:
    """Spend a challenge with a code. Returns the user (locked by the caller afterwards). A wrong code counts against the challenge
    AND the person; the challenge dies after `mfa_max_attempts_per_challenge` wrong codes."""
    if not looks_like_token(token):
        raise ChallengeInvalid()
    challenge = db.scalar(select(MfaChallenge).where(MfaChallenge.token_hash == hash_token(token)).with_for_update().execution_options(populate_existing=True))
    if challenge is None or challenge.used_at is not None or challenge.expires_at <= now or challenge.attempts >= settings.mfa_max_attempts_per_challenge:
        raise ChallengeInvalid()
    user = db.get(User, challenge.user_id)
    if user is None or not user.is_active:
        raise ChallengeInvalid()
    try:
        prove(db, user, code, now, source)
    except InvalidCode:
        challenge.attempts += 1  # prove() committed the failure event; this attempt is its own small write
        db.commit()
        raise
    challenge.used_at = now
    return user


def purge_challenges(db: Session, now: datetime, *, cutoff_days: int = 1) -> int:
    return db.execute(delete(MfaChallenge).where(MfaChallenge.expires_at < now - timedelta(days=cutoff_days))).rowcount or 0
