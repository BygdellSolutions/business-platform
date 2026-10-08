"""What "today" is for an organization.

Dates in this platform are calendar dates (a transaction date, an invoice date). When the server fills one in, it
must be the organization's local date, not the server's: at 00:30 in Stockholm it is still yesterday in UTC. An
organization without a configured time zone keeps the old behavior (UTC); nothing assumes a zone for it.
"""

import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo, available_timezones

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.models import Organization

FALLBACK_ZONE = "UTC"


def is_known_zone(name: str) -> bool:
    return name in available_timezones()


def today_in(zone_name: str | None, now: datetime | None = None) -> date:
    """The calendar date at `now` (default: the clock) in the zone, or in UTC when no zone is set."""
    moment = now if now is not None else clock.utcnow()
    return moment.astimezone(ZoneInfo(zone_name or FALLBACK_ZONE)).date()


def organization_today(db: Session, organization_id: uuid.UUID) -> date:
    zone = db.scalar(select(Organization.timezone).where(Organization.id == organization_id))
    return today_in(zone)


def organization_zone(db: Session, organization_id: uuid.UUID) -> ZoneInfo:
    zone = db.scalar(select(Organization.timezone).where(Organization.id == organization_id))
    return ZoneInfo(zone or FALLBACK_ZONE)


def as_instant(db: Session, organization_id: uuid.UUID, moment: datetime) -> datetime:
    """A moment as an aware instant: a time without an offset is read in the organization's zone (a person types
    "14:00" meaning their own afternoon); a time with an offset is kept."""
    if moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=organization_zone(db, organization_id))
