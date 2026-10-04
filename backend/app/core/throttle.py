"""Login abuse protection. No Redis: the counters are bounded index scans over `security_events`.

See docs/architecture.md, "Login abuse protection", for the reasoning. In short, an attempt is judged BEFORE any
password hashing, from recent `login_failure` events only:

  1. per source:               failures from this source in the window        >= throttle_source_max_failures
  2. per (source, identifier): failures from this source for this identifier  >= throttle_pair_max_failures
  3. per identifier, across all sources (distributed guessing):
       failures for this identifier >= throttle_identifier_max_failures  -> only a source that has ALREADY logged in
       successfully as this identifier (within the retention) may still try
  4. global valve: failure rows written in the window >= throttle_global_max_failure_events
       -> failures stop being recorded, and only known sources (as in 3) may still try

A refused attempt (429) writes nothing, so nobody can keep a block alive by hammering it: it ends when the
failures that caused it leave the window. There is NO account-level lock: an attacker who only knows an email
can neither lock the account nor extend any block on it, and a user on a source that has logged in before is
never affected by failures from other sources.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core import security_events
from app.core.config import settings


class Throttled(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after


@dataclass(frozen=True)
class LoginDecision:
    record_failures: bool  # False while the global valve is open: the table must not keep growing


def _known_source(db: Session, now: datetime, source: str, ident_hash: str) -> bool:
    since = now - timedelta(days=settings.security_event_retention_days)
    return security_events.has_event(db, "login_success", since, source=source, ident_hash=ident_hash)


def check_login(db: Session, now: datetime, source: str, ident_hash: str) -> LoginDecision:
    window = settings.throttle_window_seconds
    since = now - timedelta(seconds=window)
    count = security_events.count_recent

    if count(db, "login_failure", since, settings.throttle_source_max_failures, source=source) >= settings.throttle_source_max_failures:
        raise Throttled(window)
    if count(db, "login_failure", since, settings.throttle_pair_max_failures, source=source, ident_hash=ident_hash) >= settings.throttle_pair_max_failures:
        raise Throttled(window)

    valve_open = count(db, "login_failure", since, settings.throttle_global_max_failure_events) >= settings.throttle_global_max_failure_events
    identifier_hot = count(db, "login_failure", since, settings.throttle_identifier_max_failures, ident_hash=ident_hash) >= settings.throttle_identifier_max_failures
    if (valve_open or identifier_hot) and not _known_source(db, now, source, ident_hash):
        raise Throttled(window)
    return LoginDecision(record_failures=not valve_open)


def check_source_failures(db: Session, now: datetime, event_type: str, limit: int, source: str) -> None:
    """A plain per-source budget for the other unauthenticated or semi-authenticated operations."""
    since = now - timedelta(seconds=settings.throttle_window_seconds)
    if security_events.count_recent(db, event_type, since, limit, source=source) >= limit:
        raise Throttled(settings.throttle_window_seconds)


def check_actor_failures(db: Session, now: datetime, event_type: str, limit: int, actor_user_id) -> None:
    since = now - timedelta(seconds=settings.throttle_window_seconds)
    if security_events.count_recent(db, event_type, since, limit, actor_user_id=actor_user_id) >= limit:
        raise Throttled(settings.throttle_window_seconds)
