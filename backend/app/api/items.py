import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.deps import Pagination, pagination
from app.core.currency import share_lock_organization
from app.core.db import get_db
from app.core.query import apply_update, commit_and_refresh, contains_pattern, delete_or_409
from app.core.tenant import TenantContext, get_tenant_context
from app.core.tenant_scope import create_scoped, get_scoped_or_404, scoped_select
from app.models import Item, ItemType
from app.schemas.item import ItemCreate, ItemRead, ItemUpdate

router = APIRouter(prefix="/api/items", tags=["items"])


@router.post("", response_model=ItemRead, status_code=status.HTTP_201_CREATED)
def create_item(
    payload: ItemCreate,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> Item:
    # A price is only meaningful in the organization's currency, so an item cannot appear while
    # the currency is being changed (see app/core/currency.py). The foreign key's own key-share
    # lock on the organization row would also conflict with that change; this lock states the
    # intent explicitly instead of relying on how foreign-key locking happens to work.
    share_lock_organization(db, ctx.organization_id)
    item = create_scoped(db, ctx, Item, **payload.model_dump())
    commit_and_refresh(db, item)
    return item


@router.get("", response_model=list[ItemRead])
def list_items(
    q: str | None = Query(default=None, max_length=255, description="Name or description contains"),
    type: ItemType | None = None,
    active: bool | None = None,
    page: Pagination = Depends(pagination),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[Item]:
    query = scoped_select(Item, ctx)
    if q:
        pattern = contains_pattern(q)
        query = query.where(
            or_(Item.name.ilike(pattern, escape="\\"), Item.description.ilike(pattern, escape="\\"))
        )
    if type is not None:
        query = query.where(Item.type == type)
    if active is not None:
        query = query.where(Item.active == active)
    query = query.order_by(Item.name, Item.id).limit(page.limit).offset(page.offset)
    return list(db.scalars(query))


@router.get("/{item_id}", response_model=ItemRead)
def read_item(
    item_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> Item:
    return get_scoped_or_404(db, ctx, Item, item_id)


@router.patch("/{item_id}", response_model=ItemRead)
def update_item(
    item_id: uuid.UUID,
    payload: ItemUpdate,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> Item:
    item = get_scoped_or_404(db, ctx, Item, item_id)
    apply_update(db, item, payload.model_dump(exclude_unset=True))
    return item


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(
    item_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> Response:
    item = get_scoped_or_404(db, ctx, Item, item_id)
    delete_or_409(db, item, "Item is referenced by other records")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
