"""Generic lifecycle validation ("may this transition happen?") and effects ("it happened: act on it").

A module that owns a lifecycle (for example one with a "complete" step) calls
`ensure_valid(...)` before it performs the transition. Any capability that registered a
validator on the core registry may veto it by returning `Problem`s. The module that owns
the lifecycle never imports those capabilities, and they never import the module.

A failed validation is a 409 whose body tells the client exactly where to look:

    {"code": "validation_failed", "event": "complete", "message": "...", "total": 2,
     "problems": [{"code": "custom_field.required", "message": "...",
                   "entity_type": "<type>", "entity_id": "<uuid>", "field": "<key>", ...}]}

Validators receive the TenantContext and must only look at records of that organization,
so a problem can never describe another tenant's data.

Effects are the other half: after the owning module has written the new status, it calls
`run_effects(...)` while still holding its row lock and BEFORE it commits. A capability that
registered an effect (Inventory delivers or returns stock) does its work in that same database
transaction; it never commits, and an effect that raises rolls the whole step back. Validators
always run first, so a vetoed step never reaches an effect.
"""

from dataclasses import asdict, dataclass
from typing import Any
import uuid

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.entity_registry import registry
from app.core.tenant import TenantContext

COMPLETE = "complete"  # an entity is about to be finalized (and locked)
REOPEN = "reopen"  # a finalized entity is about to become editable again
CANCEL = "cancel"  # an entity is about to be cancelled
# A validator must ignore events it has no opinion on: the set of events grows with the modules.

MAX_PROBLEMS = 100


@dataclass(frozen=True)
class Problem:
    code: str  # machine readable, e.g. "custom_field.required"
    message: str  # human readable
    entity_type: str  # registry key of the record the problem is about
    entity_id: str
    field: str | None = None  # the field/key concerned, if any
    label: str | None = None  # its display label, if any


def collect_problems(
    db: Session, ctx: TenantContext, event: str, entity_key: str, entity_id: uuid.UUID
) -> list[Problem]:
    problems: list[Problem] = []
    for validator in registry.validators:
        problems.extend(validator(db, ctx, event, entity_key, entity_id))
    return problems


def run_effects(db: Session, ctx: TenantContext, event: str, entity_key: str, entity_id: uuid.UUID) -> None:
    """Let every registered effect act on a step that has been written but not committed.

    An effect must ignore events and entity types it has no interest in, must only touch records
    of `ctx.organization_id`, must not commit, and raises (typically an HTTPException) to refuse.
    """
    for effect in registry.effects:
        effect(db, ctx, event, entity_key, entity_id)


def ensure_valid(
    db: Session, ctx: TenantContext, event: str, entity_key: str, entity_id: uuid.UUID
) -> None:
    """Raise 409 with structured problems if any registered validator objects."""
    problems = collect_problems(db, ctx, event, entity_key, entity_id)
    if not problems:
        return
    detail: dict[str, Any] = {
        "code": "validation_failed",
        "event": event,
        "message": f"The {event} step was blocked: {len(problems)} problem(s) must be fixed first",
        "total": len(problems),
        "problems": [asdict(p) for p in problems[:MAX_PROBLEMS]],
    }
    raise HTTPException(status.HTTP_409_CONFLICT, detail=detail)
