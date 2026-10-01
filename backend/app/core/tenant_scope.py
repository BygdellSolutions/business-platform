"""Tenant-scoped data access: the way tenant-owned records are read and created.

Rule: queries for tenant-owned models start from `scoped_select` (or use
`get_scoped*`), never from a bare `select(Model)`. The organization comes from
the validated TenantContext, never from request input.

A record that belongs to another organization is indistinguishable from one
that does not exist: `get_scoped` returns None and `get_scoped_or_404` raises
the same 404 as for a random UUID.
"""

import uuid
from typing import TypeVar

from fastapi import HTTPException, status
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.core.tenant import TenantContext
from app.models.mixins import TenantOwned

T = TypeVar("T", bound=TenantOwned)


def scoped_select(model: type[T], ctx: TenantContext) -> Select[tuple[T]]:
    """SELECT from `model`, limited to the active organization."""
    return select(model).where(model.organization_id == ctx.organization_id)


def get_scoped(db: Session, ctx: TenantContext, model: type[T], record_id: uuid.UUID) -> T | None:
    return db.scalar(scoped_select(model, ctx).where(model.id == record_id))


def get_scoped_or_404(db: Session, ctx: TenantContext, model: type[T], record_id: uuid.UUID) -> T:
    record = get_scoped(db, ctx, model, record_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    return record


def create_scoped(db: Session, ctx: TenantContext, model: type[T], **fields) -> T:
    """Create a record in the active organization.

    `organization_id` is always taken from the context; passing it is an error
    so a caller can never accidentally forward a client-supplied value.
    """
    if "organization_id" in fields:
        raise ValueError("organization_id is derived from the tenant context")
    record = model(organization_id=ctx.organization_id, **fields)
    db.add(record)
    db.flush()
    return record
