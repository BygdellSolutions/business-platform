import uuid
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Notes = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]
SubjectType = Annotated[str, StringConstraints(min_length=1, max_length=64)]
Status = Literal["scheduled", "cancelled"]
LONGEST = timedelta(days=7)
# A stable can book one long session for many horses; more than this is a mistake.
MAX_SUBJECTS = 50
MAX_SERVICES = 20


def aware(value: datetime | None) -> datetime | None:
    """A time must say its offset: a bare "10:00" would mean a different moment to the server and to the browser."""
    if value is not None and value.tzinfo is None:
        raise ValueError("must include a time zone offset (for example 2026-10-16T09:00:00+02:00)")
    return value


def check_span(starts_at: datetime, ends_at: datetime) -> None:
    if ends_at <= starts_at:
        raise ValueError("ends_at must be after starts_at")
    if ends_at - starts_at > LONGEST:
        raise ValueError("an appointment can last at most 7 days")


class SubjectRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_type: SubjectType
    subject_id: uuid.UUID


def _distinct_ids(ids: list[uuid.UUID] | None) -> list[uuid.UUID] | None:
    if ids is not None and len(set(ids)) != len(ids):
        raise ValueError("the same service is listed twice")
    return ids


def _distinct(subjects: list[SubjectRef] | None) -> list[SubjectRef] | None:
    if subjects is not None and len({(s.subject_type, s.subject_id) for s in subjects}) != len(subjects):
        raise ValueError("the same record is listed twice")
    return subjects


class AppointmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    starts_at: datetime
    ends_at: datetime
    location: Short | None = None
    notes: Notes | None = None
    practitioner_user_id: uuid.UUID  # who does the work: always a real person (a member who works with bookings)
    customer_id: uuid.UUID | None = None
    subjects: list[SubjectRef] = Field(default_factory=list, max_length=MAX_SUBJECTS)
    items: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_SERVICES)  # catalog services

    _aware = field_validator("starts_at", "ends_at")(aware)
    _distinct = field_validator("subjects")(_distinct)
    _distinct_items = field_validator("items")(_distinct_ids)

    @model_validator(mode="after")
    def consistent(self):
        check_span(self.starts_at, self.ends_at)
        return self


class AppointmentUpdate(BaseModel):
    """Partial update: only fields present in the request change; the nullable ones can be cleared with null."""

    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    location: Short | None = None
    notes: Notes | None = None
    status: Status | None = None
    practitioner_user_id: uuid.UUID | None = None
    customer_id: uuid.UUID | None = None
    subjects: list[SubjectRef] | None = Field(default=None, max_length=MAX_SUBJECTS)  # the whole new list (empty clears it)
    items: list[uuid.UUID] | None = Field(default=None, max_length=MAX_SERVICES)  # the whole new list of services
    cancel_order: bool = False  # with status "cancelled": cancel the appointment's DRAFT order in the same step (it is not a stored field)

    _aware = field_validator("starts_at", "ends_at")(aware)
    _distinct = field_validator("subjects")(_distinct)
    _distinct_items = field_validator("items")(_distinct_ids)

    @field_validator("title", "starts_at", "ends_at", "status", "subjects", "items", "practitioner_user_id")
    @classmethod
    def not_null(cls, value):
        # Runs only for fields that were sent; these are required.
        if value is None:
            raise ValueError("may not be null")
        return value


class SubjectRead(BaseModel):
    subject_type: str
    subject_id: uuid.UUID
    label: str | None


class ItemRead(BaseModel):
    item_id: uuid.UUID
    name: str | None


class OrderUpdateResult(BaseModel):
    """What bringing the order up to date did (lines that came from the booking: added, removed; and lines left alone because
    somebody edited them since)."""

    added: int
    removed: int
    kept: int
    appointment: "AppointmentRead"


class AppointmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: int
    version: int
    title: str
    starts_at: datetime
    ends_at: datetime
    location: str | None
    notes: str | None
    status: str
    practitioner_user_id: uuid.UUID | None
    practitioner_name: str | None
    customer_id: uuid.UUID | None
    customer_name: str | None
    subjects: list[SubjectRead]
    items: list[ItemRead]
    order_id: uuid.UUID | None
    order_number: int | None  # None when there is no order, or it no longer exists
    order_status: str | None
    created_at: datetime
    updated_at: datetime
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None


OrderUpdateResult.model_rebuild()
