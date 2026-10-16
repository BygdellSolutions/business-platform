import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base
from app.models.mixins import Authored, Numbered, TenantOwned

SCHEDULED = "scheduled"
CANCELLED = "cancelled"
STATUSES = (SCHEDULED, CANCELLED)


class Appointment(TenantOwned, Numbered, Authored, Base):
    """A booking of time: who does it (a practitioner, a member of the organization), when, and optionally for which customer and
    about which records (its `AppointmentSubject` rows: any number of "subjects" of any registered service-subject type, stored as
    (type, id) so this module never knows what a horse is; a stable books one session for many horses). A cancelled appointment stays
    on record. Changes use `version` (If-Match) like other shared records."""

    __tablename__ = "appointments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "customer_id"], ["customers.organization_id", "customers.id"], ondelete="RESTRICT", name="fk_appointments_customer_same_organization"
        ),
        UniqueConstraint("organization_id", "id", name="uq_appointments_organization_id_id"),
        UniqueConstraint("organization_id", "number", name="uq_appointments_organization_number"),
        CheckConstraint("ends_at > starts_at", name="ck_appointments_ends_after_start"),
        CheckConstraint("ends_at - starts_at <= interval '7 days'", name="ck_appointments_at_most_a_week"),
        CheckConstraint("status IN ('" + "', '".join(STATUSES) + "')", name="ck_appointments_status"),
        CheckConstraint("version >= 1", name="ck_appointments_version_positive"),
        Index("ix_appointments_organization_starts", "organization_id", "starts_at"),
        Index("ix_appointments_organization_practitioner_starts", "organization_id", "practitioner_user_id", "starts_at"),
    )

    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    title: Mapped[str] = mapped_column(String(255))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    location: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=SCHEDULED, server_default=SCHEDULED)
    practitioner_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class AppointmentSubject(TenantOwned, Base):
    """One record an appointment is about (a horse). Polymorphic (type, id) like a service line's subject, so there is no foreign key to
    the record itself: a record with an appointment is protected from deletion by a reference guard (see registration.py). The
    appointment is a real foreign key with the organization, so a subject can never belong to another organization's appointment."""

    __tablename__ = "appointment_subjects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "appointment_id"], ["appointments.organization_id", "appointments.id"], ondelete="CASCADE", name="fk_appointment_subjects_appointment"
        ),
        UniqueConstraint("appointment_id", "subject_type", "subject_id", name="uq_appointment_subjects_once"),
        Index("ix_appointment_subjects_subject", "organization_id", "subject_type", "subject_id"),
    )

    appointment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    subject_type: Mapped[str] = mapped_column(String(64))
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
