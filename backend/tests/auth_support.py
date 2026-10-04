"""Helpers for the authentication tests: credentials, sessions and committed worlds."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core import clock, passwords, sessions
from app.core.config import settings
from app.core.db import SessionLocal, engine
from app.core.tokens import hash_token, new_token
from app.models import AuthSession, SecurityEvent, User, UserCredential
from tests.factories import make_user

PASSWORD = "correct horse battery staple"
OTHER_PASSWORD = "another quite long passphrase"


def make_credential(db: Session, user: User, password: str = PASSWORD, *, hash_value: str | None = None) -> UserCredential:
    credential = UserCredential(user_id=user.id, password_hash=hash_value or passwords.hash_password(password), password_changed_at=clock.utcnow())
    db.add(credential)
    db.flush()
    return credential


def login_user(db: Session, password: str = PASSWORD, **fields) -> User:
    """A user who can log in with `password`."""
    user = make_user(db, **fields)
    make_credential(db, user, password)
    return user


@dataclass
class Handle:
    token: str
    csrf: str
    session: AuthSession

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "X-CSRF-Token": self.csrf}

    @property
    def read_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def make_session(db: Session, user: User, *, now: datetime | None = None, absolute_days: float | None = None, last_used_ago: timedelta = timedelta(0)) -> Handle:
    """Insert a real session row directly (no login) and return its token, for tests that exercise the resolver."""
    now = now or clock.utcnow()
    token, csrf = new_token(), new_token()
    row = AuthSession(
        user_id=user.id,
        token_hash=hash_token(token),
        csrf_hash=hash_token(csrf),
        created_at=now,
        last_used_at=now - last_used_ago,
        absolute_expires_at=now + timedelta(days=settings.session_absolute_days if absolute_days is None else absolute_days),
    )
    db.add(row)
    db.flush()
    return Handle(token, csrf, row)


def login(client, email: str, password: str = PASSWORD, headers: dict[str, str] | None = None):
    return client.post("/api/auth/login", json={"email": email, "password": password}, headers=headers or {})


def events(db: Session, event_type: str | None = None) -> list[SecurityEvent]:
    query = select(SecurityEvent).order_by(SecurityEvent.id)
    if event_type:
        query = query.where(SecurityEvent.event_type == event_type)
    return list(db.scalars(query))


def active_sessions(db: Session, user: User) -> list[AuthSession]:
    return list(db.scalars(select(AuthSession).where(AuthSession.user_id == user.id, *sessions.usable(clock.utcnow()))))


# --- committed worlds (concurrency tests) ----------------------------------------------------------------------------------------------


@dataclass
class CommittedUser:
    id: uuid.UUID
    email: str
    source: str  # a unique client address, so throttling counters of different tests never meet


def build_committed_user(password: str = PASSWORD, *, with_credential: bool = True) -> CommittedUser:
    source = f"10.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}"
    with SessionLocal() as db:
        user = make_user(db)
        if with_credential:
            make_credential(db, user, password)
        committed = CommittedUser(user.id, user.email, source)
        db.commit()
    return committed


def purge_committed(users: list[CommittedUser], extra_sources: tuple[str, ...] = ()) -> None:
    """Remove everything a committed-data test created (triggers off: security events are append-only on purpose)."""
    ids = [u.id for u in users]
    sources = [u.source for u in users] + list(extra_sources)
    with engine.begin() as connection:
        connection.execute(text("set local session_replication_role = replica"))
        connection.execute(text("delete from security_events where source = any(:s) or actor_user_id = any(:u)"), {"s": sources, "u": ids})
        connection.execute(text("delete from auth_sessions where user_id = any(:u)"), {"u": ids})
        connection.execute(text("delete from user_setup_tokens where user_id = any(:u)"), {"u": ids})
        connection.execute(text("delete from user_credentials where user_id = any(:u)"), {"u": ids})
        connection.execute(text("delete from organization_users where user_id = any(:u)"), {"u": ids})
        connection.execute(text("delete from users where id = any(:u)"), {"u": ids})
