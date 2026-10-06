"""Security events and the throttling that reads them. Not the business Audit module.

Bounded by construction:
  * a row is small and fixed in shape (type from a fixed list, a source of at most 64 characters, an HMAC of
    the login identifier, a short code); it never holds a password, token or email;
  * failures are only written while the per-source, per-(source, identifier) and global budgets allow it,
    so the number of rows an attacker can cause per window is capped (see `app.core.throttle`);
  * every count is an index range scan that stops at its limit (`count_recent` selects at most `cap` rows),
    so no throttle decision can scan a large table.
Old rows are removed by `purge_events` (retention is a setting).
"""

import hashlib
import hmac
import ipaddress
from datetime import datetime, timedelta

from fastapi import Request
from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session

from app.core import internal_auth
from app.core.config import settings
from app.models.auth import SecurityEvent


def normalize_email(raw: str) -> str:
    """The one way an email is normalized for lookup and comparison: trimmed and lowercased."""
    return raw.strip().lower()


def identifier_hash(normalized_identifier: str) -> str:
    """HMAC-SHA256 of the (normalized, length-bounded) login identifier, hex. Never the identifier itself."""
    key = settings.security_key.get_secret_value().encode() if settings.security_key else b""
    return hmac.new(key, ("login-identifier:" + normalized_identifier[:320]).encode("utf-8"), hashlib.sha256).hexdigest()


def client_source(request: Request) -> str:
    """The caller's address as a throttling key: an IPv4 address, an IPv6 /64 or "unknown".

    Behind the BFF the address comes from `client_ip_header`, but ONLY with TRUST_CLIENT_IP_HEADER=true AND on a request
    that passed the BFF internal-secret check (valid only while FastAPI is reachable from the BFF alone); otherwise it
    is the connection's peer. An unauthenticated caller's header is never believed, whatever the setting.
    """
    raw = None
    if settings.trust_client_ip_header and internal_auth.is_authenticated(request.scope):
        raw = request.headers.get(settings.client_ip_header)
    if raw is None and request.client is not None:
        raw = request.client.host
    try:
        address = ipaddress.ip_address((raw or "").split(",")[0].strip())
    except ValueError:
        return "unknown"
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped  # "::ffff:203.0.113.7" is the IPv4 client, not one /64 shared by every IPv4 client
    if isinstance(address, ipaddress.IPv6Address):
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


def record(
    db: Session,
    event_type: str,
    now: datetime,
    *,
    actor_user_id=None,
    source: str | None = None,
    ident_hash: str | None = None,
    detail: str | None = None,
) -> None:
    db.add(SecurityEvent(occurred_at=now, event_type=event_type, actor_user_id=actor_user_id, source=source, identifier_hash=ident_hash, detail=detail))


def count_recent(
    db: Session,
    event_type: str,
    since: datetime,
    cap: int,
    *,
    source: str | None = None,
    ident_hash: str | None = None,
    actor_user_id=None,
) -> int:
    """How many matching events happened since `since`, counting at most `cap` (an index scan that stops there)."""
    conditions = [SecurityEvent.event_type == event_type, SecurityEvent.occurred_at > since]
    if source is not None:
        conditions.append(SecurityEvent.source == source)
    if ident_hash is not None:
        conditions.append(SecurityEvent.identifier_hash == ident_hash)
    if actor_user_id is not None:
        conditions.append(SecurityEvent.actor_user_id == actor_user_id)
    limited = select(SecurityEvent.id).where(*conditions).limit(cap).subquery()
    return db.scalar(select(func.count()).select_from(limited)) or 0


def has_event(db: Session, event_type: str, since: datetime, *, source: str, ident_hash: str) -> bool:
    return bool(
        db.scalar(
            select(
                exists().where(
                    SecurityEvent.event_type == event_type,
                    SecurityEvent.occurred_at > since,
                    SecurityEvent.source == source,
                    SecurityEvent.identifier_hash == ident_hash,
                )
            )
        )
    )


def purge_events(db: Session, now: datetime, *, batch: int = 1000, max_batches: int = 100) -> int:
    """Delete events older than the retention, in bounded batches. Returns how many were deleted."""
    cutoff = now - timedelta(days=settings.security_event_retention_days)
    deleted = 0
    for _ in range(max_batches):
        ids = select(SecurityEvent.id).where(SecurityEvent.occurred_at < cutoff).order_by(SecurityEvent.occurred_at).limit(batch)
        result = db.execute(delete(SecurityEvent).where(SecurityEvent.id.in_(ids)))
        deleted += result.rowcount or 0
        if (result.rowcount or 0) < batch:
            break
    return deleted
