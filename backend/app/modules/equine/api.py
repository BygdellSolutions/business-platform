import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import and_
from sqlalchemy.orm import Session, aliased

from app.api.deps import Pagination, pagination
from app.core.db import get_db
from app.core.query import apply_update, commit_and_refresh, contains_pattern
from app.core.tenant import TenantContext, get_tenant_context
from app.core.tenant_scope import (
    create_scoped,
    get_scoped_or_404,
    resolve_reference,
    scoped_select,
)
from app.models import Customer
from app.modules.equine.models import Horse
from app.modules.equine.schemas import HorseCreate, HorseRead, HorseUpdate
from app.schemas.customer import CustomerRef

router = APIRouter(prefix="/api/horses", tags=["horses"])

REFERENCE_FIELDS = ("owner_customer_id", "stable_customer_id")

Owner = aliased(Customer)
Stable = aliased(Customer)


def _horse_rows(ctx: TenantContext):
    """Horses of the active organization with their owner and stable customers.

    Starts from scoped_select. The joins match on (organization_id, id), the same
    pair the composite foreign keys guarantee, so they cannot cross tenants.
    """
    return (
        scoped_select(Horse, ctx)
        .add_columns(Owner, Stable)
        .join(
            Owner,
            and_(Owner.organization_id == Horse.organization_id, Owner.id == Horse.owner_customer_id),
        )
        .outerjoin(
            Stable,
            and_(
                Stable.organization_id == Horse.organization_id,
                Stable.id == Horse.stable_customer_id,
            ),
        )
    )


def _to_read(horse: Horse, owner: Customer, stable: Customer | None) -> HorseRead:
    return HorseRead(
        id=horse.id,
        name=horse.name,
        owner_customer_id=horse.owner_customer_id,
        stable_customer_id=horse.stable_customer_id,
        owner=CustomerRef.model_validate(owner),
        stable=CustomerRef.model_validate(stable) if stable is not None else None,
        birth_year=horse.birth_year,
        sex=horse.sex,
        breed=horse.breed,
        active=horse.active,
        created_at=horse.created_at,
        updated_at=horse.updated_at,
    )


def _read_one(db: Session, ctx: TenantContext, horse_id: uuid.UUID) -> HorseRead:
    # Callers have already checked the horse exists in this organization.
    row = db.execute(_horse_rows(ctx).where(Horse.id == horse_id)).one()
    return _to_read(*row)


@router.post("", response_model=HorseRead, status_code=status.HTTP_201_CREATED)
def create_horse(
    payload: HorseCreate,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> HorseRead:
    for field in REFERENCE_FIELDS:
        value = getattr(payload, field)
        if value is not None:
            resolve_reference(db, ctx, Customer, value, field)
    horse = create_scoped(db, ctx, Horse, **payload.model_dump())
    commit_and_refresh(db, horse)
    return _read_one(db, ctx, horse.id)


@router.get("", response_model=list[HorseRead])
def list_horses(
    q: str | None = Query(default=None, max_length=255, description="Name contains"),
    owner_customer_id: uuid.UUID | None = None,
    stable_customer_id: uuid.UUID | None = None,
    active: bool | None = None,
    page: Pagination = Depends(pagination),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[HorseRead]:
    # A filter id from another organization simply matches nothing in this one.
    query = _horse_rows(ctx)
    if q:
        query = query.where(Horse.name.ilike(contains_pattern(q), escape="\\"))
    if owner_customer_id is not None:
        query = query.where(Horse.owner_customer_id == owner_customer_id)
    if stable_customer_id is not None:
        query = query.where(Horse.stable_customer_id == stable_customer_id)
    if active is not None:
        query = query.where(Horse.active == active)
    query = query.order_by(Horse.name, Horse.id).limit(page.limit).offset(page.offset)
    return [_to_read(*row) for row in db.execute(query).all()]


@router.get("/{horse_id}", response_model=HorseRead)
def read_horse(
    horse_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> HorseRead:
    get_scoped_or_404(db, ctx, Horse, horse_id)
    return _read_one(db, ctx, horse_id)


@router.patch("/{horse_id}", response_model=HorseRead)
def update_horse(
    horse_id: uuid.UUID,
    payload: HorseUpdate,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> HorseRead:
    horse = get_scoped_or_404(db, ctx, Horse, horse_id)
    values = payload.model_dump(exclude_unset=True)
    for field in REFERENCE_FIELDS:
        # Only a NEW target must exist and be active. An unchanged reference stays
        # valid even if its customer was deactivated since.
        if values.get(field) is not None and values[field] != getattr(horse, field):
            resolve_reference(db, ctx, Customer, values[field], field)
    apply_update(db, horse, values)
    return _read_one(db, ctx, horse_id)


@router.delete("/{horse_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_horse(
    horse_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> Response:
    horse = get_scoped_or_404(db, ctx, Horse, horse_id)
    db.delete(horse)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
