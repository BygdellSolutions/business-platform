"""What "today" is for an organization.

Dates in this platform are calendar dates (a transaction date, an invoice date). When the server fills one in, it
must be the organization's local date, not the server's: at 00:30 in Stockholm it is still yesterday in UTC. An
organization without a configured time zone keeps the old behavior (UTC); nothing assumes a zone for it.
"""

import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, available_timezones

from fastapi import HTTPException, status
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


MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


def month_range(month: str | None, today: date) -> tuple[date, date]:
    """The days a dashboard month covers: "YYYY-MM" (None: the current month) from its first day to its last, or to
    today for the current month. A month after the current one is refused (it has no figures yet)."""
    if month is None:
        return today.replace(day=1), today
    year, number = (int(part) for part in month.split("-"))
    start = date(year, number, 1)
    if start > today:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[{"loc": ["query", "month"], "msg": "A month in the future has no figures yet", "type": "month.future"}],
        )
    following = date(year + 1, 1, 1) if number == 12 else date(year, number + 1, 1)
    return start, min(following - timedelta(days=1), today)


def year_range(any_day: date, today: date) -> tuple[date, date]:
    """The year of `any_day`: from 1 January to 31 December, or to today for the current year."""
    return date(any_day.year, 1, 1), min(date(any_day.year, 12, 31), today)
