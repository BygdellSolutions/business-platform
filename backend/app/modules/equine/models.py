import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base
from app.models.mixins import Authored, Numbered, TenantOwned

# Sanity bounds enforced by the database; the API additionally rejects future years.
MIN_BIRTH_YEAR = 1900
MAX_BIRTH_YEAR = 2100


class HorseSex(StrEnum):
    MARE = "mare"
    STALLION = "stallion"
    GELDING = "gelding"


class Horse(TenantOwned, Numbered, Authored, Base):
    """A horse. Not a customer.

    Owner and stable are two separate references to Customers of the SAME
    organization. Neither implies the other, and neither is the billing customer:
    billing belongs to the future transaction, never to the horse.

    The foreign keys are composite (organization_id, customer id), so the database
    itself refuses a reference to another organization's customer.
    """

    __tablename__ = "horses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "owner_customer_id"],
            ["customers.organization_id", "customers.id"],
            ondelete="RESTRICT",
            name="fk_horses_owner_same_organization",
        ),
        # MATCH SIMPLE (the default): a NULL stable_customer_id skips the check.
        ForeignKeyConstraint(
            ["organization_id", "stable_customer_id"],
            ["customers.organization_id", "customers.id"],
            ondelete="RESTRICT",
            name="fk_horses_stable_same_organization",
        ),
        CheckConstraint("sex IN ('" + "', '".join(HorseSex) + "')", name="ck_horses_sex"),
        CheckConstraint(
            f"birth_year BETWEEN {MIN_BIRTH_YEAR} AND {MAX_BIRTH_YEAR}",
            name="ck_horses_birth_year_range",
        ),
        # Target of composite foreign keys (a horse's notes belong to a horse of the same organization).
        UniqueConstraint("organization_id", "id", name="uq_horses_organization_id_id"),
        UniqueConstraint("organization_id", "number", name="uq_horses_organization_number"),
        # Serve the "horses of this owner/stable" filter.
        Index("ix_horses_organization_owner", "organization_id", "owner_customer_id"),
        Index("ix_horses_organization_stable", "organization_id", "stable_customer_id"),
    )

    name: Mapped[str] = mapped_column(String(255))
    owner_customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    stable_customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    birth_year: Mapped[int | None] = mapped_column(SmallInteger)
    sex: Mapped[str | None] = mapped_column(String(16))
    breed: Mapped[str | None] = mapped_column(String(100))  # free text on purpose
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class HorseNote(TenantOwned, Authored, Base):
    """A note about a horse: what was seen, done or agreed, with who wrote it and when (newest first on the page).

    Belongs to a horse of the same organization (composite foreign key) and goes with it: deleting the horse deletes
    its notes. Adding, changing and deleting a note is recorded in the horse's history.
    """

    __tablename__ = "horse_notes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "horse_id"], ["horses.organization_id", "horses.id"], ondelete="CASCADE", name="fk_horse_notes_horse"
        ),
        CheckConstraint("length(btrim(body)) > 0", name="ck_horse_notes_body_not_blank"),
        Index("ix_horse_notes_horse", "organization_id", "horse_id", "created_at"),
    )

    horse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    body: Mapped[str] = mapped_column(Text)


JOURNAL_DRAFT, JOURNAL_FINAL = "draft", "final"
JOURNAL_MAX_BODY = 20_000
JOURNAL_MAX_MARKS = 200
DIAGRAM_VERSION = 2  # which bundled horse illustration the marks were drawn on (1: a simple line drawing, 2: an engraved horse; each entry keeps its own)


class HorseJournalEntry(TenantOwned, Numbered, Authored, Base):
    """One visit in a horse's journal: when, who, what was found and done (text), and drawing marks on the horse diagram.

    A therapy/self-evaluation journal of the practitioner, NOT a formal veterinary record. It is a DRAFT (editable, with `version`
    for conflict detection) until it is finalized, after which text and marks never change; later corrections are separate dated
    additions. It belongs to a horse of the same organization (composite foreign key) and goes only with its whole organization:
    a horse is never deleted. `marks` is a list of validated drawing marks (see journal_schemas.py), at most 200.
    """

    __tablename__ = "horse_journal_entries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "horse_id"], ["horses.organization_id", "horses.id"], ondelete="CASCADE", name="fk_horse_journal_entries_horse"
        ),
        UniqueConstraint("organization_id", "id", name="uq_horse_journal_entries_organization_id_id"),
        UniqueConstraint("organization_id", "number", name="uq_horse_journal_entries_organization_number"),
        CheckConstraint("status IN ('draft', 'final')", name="ck_horse_journal_entries_status"),
        CheckConstraint("(status = 'final') = (finalized_at IS NOT NULL AND finalized_by IS NOT NULL)", name="ck_horse_journal_entries_final_stamped"),
        CheckConstraint(f"length(body) <= {JOURNAL_MAX_BODY}", name="ck_horse_journal_entries_body_length"),
        CheckConstraint("length(btrim(practitioner_name)) > 0", name="ck_horse_journal_entries_practitioner_not_blank"),
        CheckConstraint(f"jsonb_typeof(marks) = 'array' AND jsonb_array_length(marks) <= {JOURNAL_MAX_MARKS}", name="ck_horse_journal_entries_marks"),
        CheckConstraint("version >= 1 AND diagram_version >= 1", name="ck_horse_journal_entries_versions_positive"),
        Index("ix_horse_journal_entries_horse_visit", "organization_id", "horse_id", text("visit_at DESC"), "id"),
    )

    horse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    visit_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # when the visit took place (not when it was written down)
    # Who did the work: a member of the organization chosen from a list (like the person who performed a service on an order), and
    # their name as it was then (a member who leaves or is renamed does not change an old entry). NULL only for entries that
    # predate the list.
    practitioner_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    practitioner_name: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, default="", server_default="")
    follow_up_on: Mapped[date | None] = mapped_column(Date)
    marks: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    diagram_version: Mapped[int] = mapped_column(Integer, default=DIAGRAM_VERSION, server_default=text("1"))
    status: Mapped[str] = mapped_column(String(16), default=JOURNAL_DRAFT, server_default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))  # If-Match, +1 per change
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))


class HorseJournalCorrection(TenantOwned, Authored, Base):
    """A dated addition to a FINALIZED journal entry: what was wrong or has been learned since. The entry itself never changes; this is
    written once and never changed (the database refuses), with its author and time."""

    __tablename__ = "horse_journal_corrections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "entry_id"], ["horse_journal_entries.organization_id", "horse_journal_entries.id"],
            ondelete="CASCADE", name="fk_horse_journal_corrections_entry",
        ),
        CheckConstraint("length(btrim(body)) > 0 AND length(body) <= 5000", name="ck_horse_journal_corrections_body"),
        Index("ix_horse_journal_corrections_entry", "organization_id", "entry_id", "created_at"),
    )

    entry_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    body: Mapped[str] = mapped_column(Text)
