"""Generic lifecycle validation: "may this transition happen?" asked of whoever has an opinion.

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
"""

from dataclasses import asdict, dataclass
from typing import Any
import uuid

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.entity_registry import registry
from app.core.tenant import TenantContext

COMPLETE = "complete"  # an entity is about to be finalized (and locked)

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
