"""Who or what a service was performed for: a record of any registered service subject (a customer, a horse...).

Sales stores the subject as (registry key, id) and never knows what the record is; this module validates a new
subject in the active organization and resolves labels for display and invoice snapshots, using only what the
entity registered (its model and reference label). A polymorphic reference has no foreign key, so a record that is a
subject is protected from deletion by a reference guard instead (registered by Sales).
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.entity_registry import registry
from app.core.tenant import TenantContext


def _not_found(field: str) -> HTTPException:
    # One answer for an unknown type, a record of another organization and a random id: nothing to probe.
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=[{"loc": ["body", field], "msg": "The selected record was not found", "type": "reference.not_found"}],
    )


def resolve_subject(db: Session, ctx: TenantContext, entity_type: str, entity_id: uuid.UUID, *, field: str = "subject_id") -> str:
    """Validate a NEW subject (exists in this organization, is a service subject, is active) and return its label."""
    entity = registry.get(entity_type)
    if entity is None or not entity.service_subject:
        raise _not_found(field)
    model, spec = entity.model, entity.reference
    row = db.execute(
        select(getattr(model, spec.label_column), *([getattr(model, spec.active_column)] if spec.active_column else [])).where(
            model.organization_id == ctx.organization_id, model.id == entity_id
        )
    ).first()
    if row is None:
        raise _not_found(field)
    if spec.active_column and not row[1]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[{"loc": ["body", field], "msg": "The selected record is inactive", "type": "reference.inactive"}],
        )
    return row[0]


def subject_labels(db: Session, organization_id: uuid.UUID, pairs: set[tuple[str, uuid.UUID]]) -> dict[tuple[str, uuid.UUID], str]:
    """Live labels for stored subjects, of this organization only (a missing one is simply absent)."""
    labels: dict[tuple[str, uuid.UUID], str] = {}
    by_type: dict[str, list[uuid.UUID]] = {}
    for entity_type, entity_id in pairs:
        by_type.setdefault(entity_type, []).append(entity_id)
    for entity_type, ids in by_type.items():
        entity = registry.get(entity_type)
        if entity is None or entity.reference is None:
            continue
        model = entity.model
        label = getattr(model, entity.reference.label_column)
        for entity_id, text in db.execute(select(model.id, label).where(model.organization_id == organization_id, model.id.in_(ids))):
            labels[(entity_type, entity_id)] = text
    return labels
