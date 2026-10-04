"""Authentication records: credentials, sessions, setup links and security events.

None of these is tenant-owned: they belong to a user (a global identity) or to the platform. Who may do
what in an organization stays in `organization_users`; nothing here says anything about tenants.

Secrets are never stored: a session or setup token is kept only as the SHA-256 of a 256-bit random value
(high entropy, so a fast hash is enough), and a password only as an Argon2id encoded hash.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

SESSION_REVOKED_REASONS = ("logout", "rotated", "password_changed", "cap", "disabled", "setup")
SETUP_PURPOSES = ("set_password",)
# The only things a security event can say. A fixed list keeps rows small and meaningful.
SECURITY_EVENT_TYPES = (
    "login_success",
    "login_failure",
    "logout",
    "password_changed",
    "password_change_failure",
    "setup_link_issued",
    "setup_redeemed",
    "setup_failure",
    "user_disabled",
    "user_enabled",
    "organization_created",
    "capability_changed",
    "member_role_changed",
    "member_removed",
    "member_left",
    "owner_repaired",
)

HEX64 = "^[0-9a-f]{64}$"


class UserCredential(Base):
    """A user's password. A user without a row cannot log in with a password (the dev seed users have none)."""

    __tablename__ = "user_credentials"
    __table_args__ = (CheckConstraint("left(password_hash, 10) = '$argon2id$'", name="ck_user_credentials_argon2id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AuthSession(Base):
    """A server-side session. The browser holds an opaque token; only its hash is stored here."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint(f"token_hash ~ '{HEX64}' AND csrf_hash ~ '{HEX64}'", name="ck_auth_sessions_hash_shape"),
        CheckConstraint("revoked_reason IS NULL OR revoked_reason IN ('" + "', '".join(SESSION_REVOKED_REASONS) + "')", name="ck_auth_sessions_revoked_reason"),
        CheckConstraint("(revoked_at IS NULL) = (revoked_reason IS NULL)", name="ck_auth_sessions_revocation_pair"),
        Index("ix_auth_sessions_user_active", "user_id", "created_at", postgresql_where=text("revoked_at IS NULL")),
        Index("ix_auth_sessions_absolute_expires_at", "absolute_expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(32))
    user_agent: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str | None] = mapped_column(String(64))


class UserSetupToken(Base):
    """A single-use link to set a password: how the first production user starts and how an operator recovers one."""

    __tablename__ = "user_setup_tokens"
    __table_args__ = (
        CheckConstraint(f"token_hash ~ '{HEX64}'", name="ck_user_setup_tokens_hash_shape"),
        CheckConstraint("purpose IN ('" + "', '".join(SETUP_PURPOSES) + "')", name="ck_user_setup_tokens_purpose"),
        # At most one outstanding link per user and purpose: reissuing revokes the previous one.
        Index("uq_user_setup_tokens_outstanding", "user_id", "purpose", unique=True, postgresql_where=text("used_at IS NULL AND revoked_at IS NULL")),
        Index("ix_user_setup_tokens_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    purpose: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SecurityEvent(Base):
    """Minimal append-only security facts (login outcomes, setup links, password changes).

    This is NOT the business Audit module. It exists because login throttling needs durable counters and an
    investigation needs to know what happened. A row never holds a password, a session, CSRF, setup or
    invitation token, or an email: the login identifier is an HMAC. `source` is security metadata only.
    Rows are never updated; a row younger than a day cannot be deleted (trigger); older rows are purged
    after SECURITY_EVENT_RETENTION_DAYS.
    """

    __tablename__ = "security_events"
    __table_args__ = (
        CheckConstraint("event_type IN ('" + "', '".join(SECURITY_EVENT_TYPES) + "')", name="ck_security_events_type"),
        CheckConstraint(f"identifier_hash IS NULL OR identifier_hash ~ '{HEX64}'", name="ck_security_events_identifier_shape"),
        Index("ix_security_events_type_source_time", "event_type", "source", "occurred_at"),
        Index("ix_security_events_type_identifier_time", "event_type", "identifier_hash", "occurred_at"),
        Index("ix_security_events_type_time", "event_type", "occurred_at"),
        Index("ix_security_events_occurred_at", "occurred_at"),
        Index("ix_security_events_organization_time", "organization_id", "occurred_at", postgresql_where=text("organization_id IS NOT NULL")),  # event-reference
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event_type: Mapped[str] = mapped_column(String(40))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    # The organization the event is about, when there is one: an id only (no foreign key, so an event never blocks or
    # follows the deletion of an organization). Never a name or any form content.
    # Which organization an event is ABOUT (set only by organization creation; no foreign key, so an event outlives
    # anything). A reference for readers of the log, never used to decide access.
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # event-reference
    source: Mapped[str | None] = mapped_column(String(64))
    identifier_hash: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[str | None] = mapped_column(String(64))
