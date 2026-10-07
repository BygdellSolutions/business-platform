import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.deps import Pagination, pagination
from app.core.authz import record_writer
from app.core.db import get_db
from app.core.query import apply_update, commit_and_refresh, contains_pattern, delete_or_409
from app.core.tenant import TenantContext, get_tenant_context
from app.core.tenant_scope import create_scoped, get_scoped_or_404, scoped_select
from app.models import Customer
from app.schemas.customer import CustomerCreate, CustomerRead, CustomerUpdate

router = APIRouter(prefix="/api/customers", tags=["customers"])


@router.post("", response_model=CustomerRead, status_code=status.HTTP_201_CREATED)
def create_customer(
    payload: CustomerCreate,
    ctx: TenantContext = Depends(record_writer),
    db: Session = Depends(get_db),
) -> Customer:
    customer = create_scoped(db, ctx, Customer, **payload.model_dump())
    commit_and_refresh(db, customer)
    return customer


@router.get("", response_model=list[CustomerRead])
def list_customers(
    q: str | None = Query(default=None, max_length=255, description="Name or email contains"),
    active: bool | None = None,
    page: Pagination = Depends(pagination),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[Customer]:
    query = scoped_select(Customer, ctx)
    if q:
        pattern = contains_pattern(q)
        query = query.where(
            or_(
                Customer.name.ilike(pattern, escape="\\"),
                Customer.email.ilike(pattern, escape="\\"),
            )
        )
    if active is not None:
        query = query.where(Customer.active == active)
    query = query.order_by(Customer.name, Customer.id).limit(page.limit).offset(page.offset)
    return list(db.scalars(query))


@router.get("/{customer_id}", response_model=CustomerRead)
def read_customer(
    customer_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> Customer:
    return get_scoped_or_404(db, ctx, Customer, customer_id)


@router.patch("/{customer_id}", response_model=CustomerRead)
def update_customer(
    customer_id: uuid.UUID,
    payload: CustomerUpdate,
    ctx: TenantContext = Depends(record_writer),
    db: Session = Depends(get_db),
) -> Customer:
    customer = get_scoped_or_404(db, ctx, Customer, customer_id)
    apply_update(db, customer, payload.model_dump(exclude_unset=True))
    return customer


@router.delete("/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_customer(
    customer_id: uuid.UUID,
    ctx: TenantContext = Depends(record_writer),
    db: Session = Depends(get_db),
) -> Response:
    customer = get_scoped_or_404(db, ctx, Customer, customer_id)
    delete_or_409(db, customer, "Customer is referenced by other records")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
