"""Request and response shapes of the horse journal, and the validation of its drawing marks.

A mark is a small structured description of something drawn on one of the two horse diagrams, never a picture: its own id (so a
client can edit it later), the side of the horse it is drawn on, a tool and one or two points in NORMALIZED coordinates (0 to 1 of
the diagram's width and height, so it stays aligned at any size). Nothing else is accepted: unknown fields, other tools, the wrong
number of points, coordinates outside the diagram and more than JOURNAL_MAX_MARKS marks are all refused.
"""

import math
import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.modules.equine.models import JOURNAL_MAX_BODY, JOURNAL_MAX_MARKS

Body = Annotated[str, StringConstraints(max_length=JOURNAL_MAX_BODY)]

Side = Literal["left", "right"]  # the horse's own left and right side
Tool = Literal["line", "arrow", "x", "o"]
POINTS_OF = {"line": 2, "arrow": 2, "x": 1, "o": 1}


class Mark(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    # The mark's number within its entry, so the text can say "see mark 3". Given by the browser when a mark is drawn and kept for
    # as long as the entry exists (deleting mark 2 does not turn mark 3 into 2); a mark that arrives without one is numbered by the
    # backend (journal.py). Unique within an entry.
    number: int | None = Field(default=None, ge=1, le=9999)
    side: Side
    tool: Tool
    points: list[Annotated[list[float], Field(min_length=2, max_length=2)]]

    @field_validator("points")
    @classmethod
    def _inside_the_diagram(cls, points: list[list[float]]) -> list[list[float]]:
        for x, y in points:
            if not (math.isfinite(x) and math.isfinite(y) and 0 <= x <= 1 and 0 <= y <= 1):
                raise ValueError("coordinates are fractions of the diagram, between 0 and 1")
        return points

    @model_validator(mode="after")
    def _right_number_of_points(self) -> "Mark":
        if len(self.points) != POINTS_OF[self.tool]:
            raise ValueError(f"a {self.tool} mark has {POINTS_OF[self.tool]} point(s)")
        return self


def _marks(value: list[Mark]) -> list[Mark]:
    if len(value) > JOURNAL_MAX_MARKS:
        raise ValueError(f"at most {JOURNAL_MAX_MARKS} marks per entry")
    if len({mark.id for mark in value}) != len(value):
        raise ValueError("mark ids must be unique within an entry")
    numbers = [mark.number for mark in value if mark.number is not None]
    if len(set(numbers)) != len(numbers):
        raise ValueError("mark numbers must be unique within an entry")
    return value


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError("the visit time must say its time zone (for example 2026-10-15T14:30:00+02:00)")
    return value


class JournalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visit_at: datetime | None = None  # defaults to now
    practitioner_user_id: uuid.UUID | None = None  # a member of the organization; defaults to the author
    body: Body = ""
    follow_up_on: date | None = None
    marks: list[Mark] = Field(default_factory=list, max_length=JOURNAL_MAX_MARKS)

    _aware = field_validator("visit_at")(_aware)
    _marks = field_validator("marks")(_marks)


class JournalUpdate(BaseModel):
    """Partial change of a DRAFT; only the fields present are changed."""

    model_config = ConfigDict(extra="forbid")

    visit_at: datetime | None = None
    practitioner_user_id: uuid.UUID | None = None
    body: Body | None = None
    follow_up_on: date | None = None
    marks: list[Mark] | None = Field(default=None, max_length=JOURNAL_MAX_MARKS)

    _aware = field_validator("visit_at")(_aware)

    @field_validator("marks")
    @classmethod
    def _marks_ok(cls, value: list[Mark] | None) -> list[Mark] | None:
        return None if value is None else _marks(value)

    @model_validator(mode="after")
    def _not_null(self) -> "JournalUpdate":
        for field in ("visit_at", "practitioner_user_id", "body", "marks"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be cleared")
        return self


class CorrectionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]


class CorrectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entry_id: uuid.UUID
    body: str
    created_at: datetime
    created_by: uuid.UUID | None
    created_by_name: str | None = None


class JournalSummary(BaseModel):
    """A row of the journal list: enough to find an entry; the text and marks come with the entry itself."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: int  # per organization, from the database
    horse_id: uuid.UUID
    visit_at: datetime
    practitioner_name: str
    status: str
    follow_up_on: date | None
    mark_count: int
    preview: str
    version: int
    created_by: uuid.UUID | None
    created_by_name: str | None = None
    horse_name: str | None = None  # filled in the organization-wide lists (the drafts overview), where the horse is not known from the address


class DraftOverview(BaseModel):
    drafts: int  # entries not finalized yet that this person can finish


class JournalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: int  # per organization, from the database
    horse_id: uuid.UUID
    visit_at: datetime
    practitioner_user_id: uuid.UUID | None
    practitioner_name: str
    body: str
    follow_up_on: date | None
    marks: list[Mark]
    diagram_version: int
    next_mark_number: int  # the number the next new mark gets (a number is never reused within an entry)
    status: str
    version: int  # send back as If-Match when changing or finalizing a draft
    finalized_at: datetime | None
    finalized_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None
    created_by_name: str | None = None
    finalized_by_name: str | None = None
    # Whether the signed-in person may change or finalize this draft (its author, an admin or an owner): the browser only
    # presents it, the backend decides again on every request.
    can_change: bool = False
    # Dated additions to a finalized entry, oldest first (a draft has none).
    corrections: list[CorrectionRead] = Field(default_factory=list)
    can_correct: bool = False
