"""The change history of one business record, for any member of its organization.

Events of the record itself and of records that belong to it (a transaction's lines) come back newest first,
with the name of whoever made each change. The organization comes from the membership, so a record of another
organization simply has no history here.
"""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.tenant import TenantContext, get_tenant_context
from app.models import AuditEvent, User

router = APIRouter(prefix="/api/history", tags=["history"])

MAX_EVENTS = 500


class Person(BaseModel):
    id: uuid.UUID
    name: str


class HistoryEvent(BaseModel):
    id: int
    occurred_at: datetime
    actor: Person | None
    entity_type: str
    entity_id: uuid.UUID
    action: str
    changes: dict[str, Any]


class History(BaseModel):
    events: list[HistoryEvent]
    # Everyone named in `events`, so a screen can show "created by" / "last changed by" from a record's ids.
    people: list[Person]


@router.get("", response_model=History)
def read_history(
    entity_type: str = Query(min_length=1, max_length=64),
    entity_id: uuid.UUID = Query(),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> History:
    rows = db.execute(
        select(AuditEvent, User)
        .outerjoin(User, User.id == AuditEvent.actor_user_id)
        .where(
            AuditEvent.organization_id == ctx.organization_id,
            or_(
                and_(AuditEvent.entity_type == entity_type, AuditEvent.entity_id == entity_id),
                and_(AuditEvent.context_type == entity_type, AuditEvent.context_id == entity_id),
            ),
        )
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
        .limit(MAX_EVENTS)
    ).all()
    people: dict[uuid.UUID, Person] = {}
    events = []
    for event, user in rows:
        actor = None
        if user is not None:
            actor = people.setdefault(user.id, Person(id=user.id, name=user.name))
        events.append(
            HistoryEvent(
                id=event.id, occurred_at=event.occurred_at, actor=actor, entity_type=event.entity_type,
                entity_id=event.entity_id, action=event.action, changes=event.changes,
            )
        )
    return History(events=events, people=list(people.values()))
