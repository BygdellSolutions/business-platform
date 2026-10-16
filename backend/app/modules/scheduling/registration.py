import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.entity_registry import Registry
from app.modules.scheduling.models import Appointment


def _subjects_are_booked(db: Session, entity_key: str, organization_id: uuid.UUID, record_id: uuid.UUID) -> bool:
    """A record that an appointment is about cannot be deleted (a polymorphic reference has no foreign key): deactivate it instead."""
    return db.scalar(
        select(Appointment.id).where(
            Appointment.organization_id == organization_id, Appointment.subject_type == entity_key, Appointment.subject_id == record_id
        ).limit(1)
    ) is not None


def register(registry: Registry) -> None:
    registry.add_reference_guard(_subjects_are_booked)
