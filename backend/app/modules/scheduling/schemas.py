import uuid
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator, model_validator

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Notes = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]
SubjectType = Annotated[str, StringConstraints(min_length=1, max_length=64)]
Status = Literal["scheduled", "cancelled"]
LONGEST = timedelta(days=7)


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


class AppointmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    starts_at: datetime
    ends_at: datetime
    location: Short | None = None
    notes: Notes | None = None
    practitioner_user_id: uuid.UUID | None = None
    customer_id: uuid.UUID | None = None
    subject_type: SubjectType | None = None
    subject_id: uuid.UUID | None = None

    _aware = field_validator("starts_at", "ends_at")(aware)

    @model_validator(mode="after")
    def consistent(self):
        check_span(self.starts_at, self.ends_at)
        if (self.subject_type is None) != (self.subject_id is None):
            raise ValueError("subject_type and subject_id go together")
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
    subject_type: SubjectType | None = None
    subject_id: uuid.UUID | None = None

    _aware = field_validator("starts_at", "ends_at")(aware)

    @field_validator("title", "starts_at", "ends_at", "status")
    @classmethod
    def not_null(cls, value):
        # Runs only for fields that were sent; these are required.
        if value is None:
            raise ValueError("may not be null")
        return value

    @model_validator(mode="after")
    def subject_changes_together(self):
        if ("subject_type" in self.model_fields_set) != ("subject_id" in self.model_fields_set):
            raise ValueError("subject_type and subject_id change together")
        return self


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
    subject_type: str | None
    subject_id: uuid.UUID | None
    subject_label: str | None
    created_at: datetime
    updated_at: datetime
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None
