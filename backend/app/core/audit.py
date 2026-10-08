"""Record who changed what in a business record, with the old and new values.

Every write that changes a business record calls one of these in the SAME database transaction as the change, so
history and data cannot disagree. Modules pass their own entity type (a registry key such as "customer"); this
module knows nothing about what the records are.

Values are stored JSON-safe: decimals and dates as strings exactly as the database holds them, ids as strings.
Technical columns (ids, organization, timestamps, authors, concurrency versions) are not part of a change.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from collections.abc import Iterable
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.core.tenant import TenantContext
from app.models.audit import AuditEvent

CREATED = "created"
UPDATED = "updated"
DELETED = "deleted"

NOT_A_CHANGE = frozenset(
    {"id", "organization_id", "created_at", "updated_at", "created_by", "updated_by", "version", "header_version", "position"}
)


def json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    return str(value)


def snapshot(record: Any, fields: Iterable[str] | None = None) -> dict[str, Any]:
    """The record's own column values that a person can change, JSON-safe (or only `fields`, for records that
    also hold large derived documents, such as an invoice's frozen snapshots)."""
    keys = list(fields) if fields is not None else [c.key for c in inspect(record).mapper.column_attrs if c.key not in NOT_A_CHANGE]
    return {key: json_value(getattr(record, key)) for key in keys}


def changes_between(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        field: {"from": before.get(field), "to": after.get(field)}
        for field in sorted(set(before) | set(after))
        if before.get(field) != after.get(field)
    }


def record(
    db: Session,
    ctx: TenantContext,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    action: str,
    changes: dict[str, dict[str, Any]] | None = None,
    context: tuple[str, uuid.UUID] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        organization_id=ctx.organization_id,
        actor_user_id=ctx.user.id,
        entity_type=entity_type,
        entity_id=entity_id,
        context_type=context[0] if context else None,
        context_id=context[1] if context else None,
        action=action,
        changes=changes or {},
    )
    db.add(event)
    return event


def stamp(target: Any, ctx: TenantContext, *, created: bool = False) -> None:
    """Set the author columns: both on creation, `updated_by` on every later change."""
    if created:
        target.created_by = ctx.user.id
    target.updated_by = ctx.user.id


def created(
    db: Session, ctx: TenantContext, target: Any, entity_type: str, *,
    context: tuple[str, uuid.UUID] | None = None, fields: Iterable[str] | None = None,
) -> None:
    """A new record (already added and flushed): stamp it and record every value it was created with."""
    stamp(target, ctx, created=True)
    values = snapshot(target, fields)
    record(
        db, ctx, entity_type=entity_type, entity_id=target.id, action=CREATED,
        changes={field: {"from": None, "to": value} for field, value in values.items() if value is not None},
        context=context,
    )


def updated(
    db: Session, ctx: TenantContext, target: Any, entity_type: str, before: dict[str, Any], *,
    action: str = UPDATED, context: tuple[str, uuid.UUID] | None = None, fields: Iterable[str] | None = None,
) -> bool:
    """After attributes were set on `target`: record what differs from `before` (a `snapshot`). A write that
    changed nothing records nothing and stamps nothing. Returns whether anything changed."""
    changes = changes_between(before, snapshot(target, fields))
    if not changes:
        return False
    stamp(target, ctx)
    record(db, ctx, entity_type=entity_type, entity_id=target.id, action=action, changes=changes, context=context)
    return True


def deletion(
    db: Session, ctx: TenantContext, target: Any, entity_type: str, *,
    context: tuple[str, uuid.UUID] | None = None, fields: Iterable[str] | None = None,
):
    """For deletions that may still be refused: takes the values now and returns a function that records the
    event, to be called only once the row is really gone (see `delete_or_409(after_delete=...)`)."""
    values = snapshot(target, fields)
    entity_id = target.id

    def record_it() -> None:
        record(
            db, ctx, entity_type=entity_type, entity_id=entity_id, action=DELETED,
            changes={field: {"from": value, "to": None} for field, value in values.items() if value is not None},
            context=context,
        )

    return record_it


def deleted(
    db: Session, ctx: TenantContext, target: Any, entity_type: str, *,
    context: tuple[str, uuid.UUID] | None = None, fields: Iterable[str] | None = None,
) -> None:
    """Before deleting `target`: record the values it had. If the deletion is refused, the request's transaction
    rolls back and this event with it."""
    values = snapshot(target, fields)
    record(
        db, ctx, entity_type=entity_type, entity_id=target.id, action=DELETED,
        changes={field: {"from": value, "to": None} for field, value in values.items() if value is not None},
        context=context,
    )


def apply_audited_update(
    db: Session, ctx: TenantContext, target: Any, entity_type: str, values: dict[str, Any], *,
    context: tuple[str, uuid.UUID] | None = None,
) -> bool:
    """`apply_update` with history: set the values, record what actually changed, persist. Returns whether
    anything changed."""
    from app.core.query import commit_and_refresh  # query imports the registry; keep this module light

    before = snapshot(target)
    for field, value in values.items():
        setattr(target, field, value)
    # Compare what the database STORED (900 becomes 900.00, a trimmed name stays trimmed), not what was sent.
    db.flush()
    db.refresh(target)
    changed = updated(db, ctx, target, entity_type, before, context=context)
    commit_and_refresh(db, target)
    return changed
