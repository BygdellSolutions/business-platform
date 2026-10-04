"""Server-side sessions: an opaque token in the browser, only its hash here.

Resolution (`authenticate_request`) accepts exactly `Authorization: Bearer <43-character token>`. The token's
SHA-256 is looked up (unique index) and the session must be unrevoked, within both its absolute and its idle
lifetime, and belong to an ACTIVE user, all checked on every request: disabling a user takes effect on their
next request. A mutating request (anything but GET, HEAD, OPTIONS) must also carry `X-CSRF-Token` whose
SHA-256 equals the session's `csrf_hash` (constant-time comparison). The browser's Origin and cookies are
the BFF's concern and are never looked at here.
"""

import hmac
from datetime import datetime, timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import settings
from app.core.tokens import hash_token, looks_like_token, new_token
from app.models import User
from app.models.auth import AuthSession, UserSetupToken

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_HEADER = "X-CSRF-Token"


def _idle(now: datetime) -> datetime:
    return now - timedelta(hours=settings.session_idle_hours)


def usable(now: datetime):
    """SQL conditions of a session that may still authenticate (user activity is checked with it)."""
    return (
        AuthSession.revoked_at.is_(None),
        AuthSession.absolute_expires_at > now,
        AuthSession.last_used_at > _idle(now),
    )


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if header is None:
        return None
    scheme, _, value = header.partition(" ")
    value = value.strip()
    if scheme.lower() != "bearer" or not looks_like_token(value):
        return None
    return value


def find_usable(db: Session, token: str, now: datetime) -> tuple[AuthSession, User] | None:
    row = db.execute(
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(AuthSession.token_hash == hash_token(token), *usable(now), User.is_active.is_(True))
    ).first()
    return None if row is None else (row[0], row[1])


def _csrf_failed() -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "csrf_failed", "message": "The request could not be verified."})


def authenticate_request(request: Request, db: Session) -> User | None:
    """The user of the request's session, or None (never a fallback to anything else)."""
    token = bearer_token(request)
    if token is None:
        return None
    now = clock.utcnow()
    found = find_usable(db, token, now)
    if found is None:
        return None
    session, user = found
    if request.method.upper() not in SAFE_METHODS:
        supplied = request.headers.get(CSRF_HEADER)
        if not looks_like_token(supplied) or not hmac.compare_digest(hash_token(supplied), session.csrf_hash):
            raise _csrf_failed()
    if now - session.last_used_at >= timedelta(seconds=settings.session_touch_seconds):
        db.execute(update(AuthSession).where(AuthSession.id == session.id).values(last_used_at=now))
        db.commit()
    request.state.auth_session = session
    return user


def current_session(request: Request) -> AuthSession:
    session = getattr(request.state, "auth_session", None)
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return session


def revoke(db: Session, session_id, reason: str, now: datetime) -> None:
    db.execute(update(AuthSession).where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None)).values(revoked_at=now, revoked_reason=reason))


def revoke_all_for_user(db: Session, user_id, reason: str, now: datetime, *, except_session_id=None) -> int:
    conditions = [AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)]
    if except_session_id is not None:
        conditions.append(AuthSession.id != except_session_id)
    return db.execute(update(AuthSession).where(*conditions).values(revoked_at=now, revoked_reason=reason)).rowcount or 0


def create_session(db: Session, user: User, now: datetime, *, user_agent: str | None, source: str | None) -> tuple[AuthSession, str, str]:
    """A fresh session for `user` (the caller holds the user row locked): returns (row, token, csrf token).

    Enforces the per-user cap by revoking the oldest sessions. The raw tokens exist only in the return value.
    """
    token, csrf = new_token(), new_token()
    session = AuthSession(
        user_id=user.id,
        token_hash=hash_token(token),
        csrf_hash=hash_token(csrf),
        created_at=now,
        last_used_at=now,
        absolute_expires_at=now + timedelta(days=settings.session_absolute_days),
        user_agent=user_agent[:200] if user_agent else None,
        source=source,
    )
    db.add(session)
    db.flush()
    active = list(
        db.scalars(select(AuthSession).where(AuthSession.user_id == user.id, *usable(now)).order_by(AuthSession.created_at, AuthSession.id))
    )
    for oldest in active[: max(0, len(active) - settings.session_max_per_user)]:
        revoke(db, oldest.id, "cap", now)
    return session, token, csrf


def purge_records(db: Session, now: datetime, *, batch: int = 1000, max_batches: int = 100) -> dict[str, int]:
    """Delete sessions that ended, and setup tokens that were used, revoked or expired, more than the retention ago."""
    cutoff = now - timedelta(days=settings.auth_record_retention_days)
    ended_session = (AuthSession.absolute_expires_at < cutoff) | (AuthSession.revoked_at < cutoff) | (AuthSession.last_used_at < cutoff - timedelta(hours=settings.session_idle_hours))
    ended_token = (UserSetupToken.expires_at < cutoff) | (UserSetupToken.used_at < cutoff) | (UserSetupToken.revoked_at < cutoff)
    counts = {}
    for name, model, ended in (("sessions", AuthSession, ended_session), ("setup_tokens", UserSetupToken, ended_token)):
        total = 0
        for _ in range(max_batches):
            ids = select(model.id).where(ended).limit(batch)
            result = db.execute(delete(model).where(model.id.in_(ids)))
            total += result.rowcount or 0
            if (result.rowcount or 0) < batch:
                break
        counts[name] = total
    return counts
