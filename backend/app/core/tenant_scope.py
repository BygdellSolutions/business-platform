"""Tenant-scoped data access: the way tenant-owned records are read and created.

Rule: queries for tenant-owned models start from `scoped_select` (or use
`get_scoped*`), never from a bare `select(Model)`. The organization comes from
the validated TenantContext, never from request input.

A record that belongs to another organization is indistinguishable from one
that does not exist: `get_scoped` returns None and `get_scoped_or_404` raises
the same 404 as for a random UUID.

References between records (a transaction's billing customer, an item on a sale line)
are validated with `resolve_reference`, which applies the same rule to ids that
arrive in a request body.
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


def get_scoped(
    db: Session,
    ctx: TenantContext,
    model: type[T],
    record_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> T | None:
    """`for_update` locks the row (SELECT ... FOR UPDATE) until the transaction ends."""
    query = scoped_select(model, ctx).where(model.id == record_id)
    if for_update:
        query = query.with_for_update()
    return db.scalar(query)


def get_scoped_or_404(
    db: Session,
    ctx: TenantContext,
    model: type[T],
    record_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> T:
    record = get_scoped(db, ctx, model, record_id, for_update=for_update)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    return record


def resolve_reference(
    db: Session,
    ctx: TenantContext,
    model: type[T],
    record_id: uuid.UUID,
    field: "str | tuple[str | int, ...]",
    *,
    label: str | None = None,
    require_active: bool = True,
) -> T:
    """Validate that a referenced record exists in the ACTIVE organization.

    Raises a 422 on `field` (a body field name, or a path such as ("lines", 0, "item_id")
    for nested bodies). A record in another organization and a nonexistent id
    produce exactly the same error, so a reference cannot be used to probe other
    tenants. Use it whenever a request body contains the id of another tenant-owned
    record. `require_active` rejects records that have been deactivated; callers
    skip the check for references that did not change (existing links stay valid).
    """
    label = label or model.__name__
    record = get_scoped(db, ctx, model, record_id)
    if record is None:
        reference_error(field, f"{label} not found", "reference.not_found")
    if require_active and not getattr(record, "active", True):
        reference_error(field, f"{label} is inactive", "reference.inactive")
    return record


def reference_error(field: "str | tuple[str | int, ...]", message: str, error_type: str):
    # Same shape as FastAPI's own validation errors, so clients handle both alike.
    path = [field] if isinstance(field, str) else list(field)
    raise HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=[{"loc": ["body", *path], "msg": message, "type": error_type}],
    )


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
