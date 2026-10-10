import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.deps import Pagination, pagination
from app.core import audit, discounts
from app.core.prices import discounted_unit_price, price_ex_vat_from_inc, price_inc_vat
from app.core.authz import record_writer, roles_required
from app.core.currency import share_lock_organization
from app.core.db import get_db
from app.core.org_time import organization_today
from app.core.query import commit_and_refresh, contains_pattern, delete_or_409
from app.core.tenant import TenantContext, get_tenant_context
from app.core.tenant_scope import create_scoped, get_scoped_or_404, reference_error, scoped_select
from app.models import Item, ItemDiscount, ItemType, Role
from app.schemas.item import ItemCreate, ItemDiscountCreate, ItemDiscountRead, ItemRead, ItemUpdate

router = APIRouter(prefix="/api/items", tags=["items"])


@router.post("", response_model=ItemRead, status_code=status.HTTP_201_CREATED)
def create_item(
    payload: ItemCreate,
    ctx: TenantContext = Depends(record_writer),
    db: Session = Depends(get_db),
) -> ItemRead:
    # A price is only meaningful in the organization's currency, so an item cannot appear while
    # the currency is being changed (see app/core/currency.py). The foreign key's own key-share
    # lock on the organization row would also conflict with that change; this lock states the
    # intent explicitly instead of relying on how foreign-key locking happens to work.
    share_lock_organization(db, ctx.organization_id)
    _ensure_sku_free(db, ctx, payload.sku, None)
    values = payload.model_dump()
    if values.pop("price_inc_vat") is not None:
        values["price_ex_vat"] = price_ex_vat_from_inc(payload.price_inc_vat, payload.vat_rate)
    item = create_scoped(db, ctx, Item, **values)
    audit.created(db, ctx, item, "item")
    commit_and_refresh(db, item)
    return _with_discounts(db, ctx, [item])[0]


@router.get("", response_model=list[ItemRead])
def list_items(
    q: str | None = Query(default=None, max_length=255, description="Name or description contains"),
    type: ItemType | None = None,
    active: bool | None = None,
    page: Pagination = Depends(pagination),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[ItemRead]:
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
    return _with_discounts(db, ctx, list(db.scalars(query)))


@router.get("/{item_id}", response_model=ItemRead)
def read_item(
    item_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> ItemRead:
    return _with_discounts(db, ctx, [get_scoped_or_404(db, ctx, Item, item_id)])[0]


@router.patch("/{item_id}", response_model=ItemRead)
def update_item(
    item_id: uuid.UUID,
    payload: ItemUpdate,
    ctx: TenantContext = Depends(record_writer),
    db: Session = Depends(get_db),
) -> ItemRead:
    item = get_scoped_or_404(db, ctx, Item, item_id)
    values = payload.model_dump(exclude_unset=True)
    # The rule is about the item as it will be, so a change of type alone is checked too.
    if values.get("track_stock", item.track_stock) and values.get("type", item.type) != ItemType.PRODUCT:
        reference_error("track_stock", "Only a product can track stock", "item.track_stock_service")
    if "sku" in values:
        _ensure_sku_free(db, ctx, values["sku"], item.id)
    if "price_inc_vat" in values:
        values["price_ex_vat"] = price_ex_vat_from_inc(values.pop("price_inc_vat"), values.get("vat_rate", item.vat_rate))
    audit.apply_audited_update(db, ctx, item, "item", values)
    return _with_discounts(db, ctx, [item])[0]


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(
    item_id: uuid.UUID,
    ctx: TenantContext = Depends(record_writer),
    db: Session = Depends(get_db),
) -> Response:
    item = get_scoped_or_404(db, ctx, Item, item_id)
    delete_or_409(db, item, "Item is referenced by other records", after_delete=audit.deletion(db, ctx, item, "item"))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _ensure_sku_free(db: Session, ctx: TenantContext, sku: str | None, item_id: uuid.UUID | None) -> None:
    """An article number names one item of the organization (the unique index is the backstop for a race)."""
    if sku is None:
        return
    query = scoped_select(Item, ctx).where(Item.sku == sku)
    if item_id is not None:
        query = query.where(Item.id != item_id)
    if db.scalar(query) is not None:
        reference_error("sku", "Another item already has this article number", "item.sku_taken")


def _with_discounts(db: Session, ctx: TenantContext, items: list[Item]) -> list[ItemRead]:
    """Items as read, each with the temporary discount active today in the organization's time zone."""
    active = discounts.active_item_discounts(db, ctx.organization_id, [item.id for item in items], organization_today(db, ctx.organization_id))
    return [_priced(item, active.get(item.id)) for item in items]


def _priced(item: Item, discount: ItemDiscount | None) -> ItemRead:
    promotion = discounted_unit_price(item.price_ex_vat, discount.percent, None) if discount is not None else None
    return ItemRead.model_validate(item).model_copy(
        update={
            "current_discount": ItemDiscountRead.model_validate(discount) if discount is not None else None,
            "price_inc_vat": price_inc_vat(item.price_ex_vat, item.vat_rate),
            "promotion_price_ex_vat": promotion,
            "promotion_price_inc_vat": price_inc_vat(promotion, item.vat_rate) if promotion is not None else None,
        }
    )


# --- temporary discounts: reading for every member, changing for owners and admins (a pricing decision) ------------

discount_admin = roles_required(Role.OWNER, Role.ADMIN)


@router.get("/{item_id}/discounts", response_model=list[ItemDiscountRead])
def list_item_discounts(
    item_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)
) -> list[ItemDiscount]:
    get_scoped_or_404(db, ctx, Item, item_id)
    query = scoped_select(ItemDiscount, ctx).where(ItemDiscount.item_id == item_id).order_by(ItemDiscount.starts_on.desc())
    return list(db.scalars(query))


@router.post("/{item_id}/discounts", response_model=ItemDiscountRead, status_code=status.HTTP_201_CREATED)
def create_item_discount(
    item_id: uuid.UUID, payload: ItemDiscountCreate, ctx: TenantContext = Depends(discount_admin), db: Session = Depends(get_db)
) -> ItemDiscount:
    # The item's row lock serializes discount changes of one item, so two overlapping periods cannot both pass.
    get_scoped_or_404(db, ctx, Item, item_id, for_update=True)
    overlap = db.scalar(
        scoped_select(ItemDiscount, ctx).where(
            ItemDiscount.item_id == item_id,
            or_(ItemDiscount.ends_on.is_(None), ItemDiscount.ends_on >= payload.starts_on),
            *([ItemDiscount.starts_on <= payload.ends_on] if payload.ends_on is not None else []),
        )
    )
    if overlap is not None:
        reference_error("starts_on", "This period overlaps another discount of this item", "discount.overlap")
    discount = create_scoped(db, ctx, ItemDiscount, item_id=item_id, **payload.model_dump())
    audit.created(db, ctx, discount, "item_discount", context=("item", item_id))
    commit_and_refresh(db, discount)
    return discount


@router.delete("/{item_id}/discounts/{discount_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item_discount(
    item_id: uuid.UUID, discount_id: uuid.UUID, ctx: TenantContext = Depends(discount_admin), db: Session = Depends(get_db)
) -> Response:
    get_scoped_or_404(db, ctx, Item, item_id, for_update=True)
    discount = db.scalar(scoped_select(ItemDiscount, ctx).where(ItemDiscount.id == discount_id, ItemDiscount.item_id == item_id))
    if discount is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    audit.deleted(db, ctx, discount, "item_discount", context=("item", item_id))
    db.delete(discount)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
