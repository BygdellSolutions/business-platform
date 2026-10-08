import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.deps import Pagination, pagination
from app.core import audit
from app.core.authz import record_writer, require_role
from app.core.db import get_db
from app.core.query import commit_and_refresh, contains_pattern, delete_or_409
from app.core.tenant import TenantContext, get_tenant_context
from app.core.tenant_scope import create_scoped, get_scoped_or_404, scoped_select
from app.models import Customer, Role
from app.schemas.customer import CustomerCreate, CustomerRead, CustomerUpdate

router = APIRouter(prefix="/api/customers", tags=["customers"])

# A customer's permanent discount is a pricing decision: owners and admins only, whoever else may edit the customer.
DISCOUNT_ROLES = (Role.OWNER, Role.ADMIN)


def _require_discount_authority(ctx: TenantContext, values: dict, current=None) -> None:
    """Only a request that sets or changes the discount needs the authority (an untouched or still-empty one does not)."""
    if "default_discount_percent" in values and values["default_discount_percent"] != current:
        require_role(ctx, DISCOUNT_ROLES)


@router.post("", response_model=CustomerRead, status_code=status.HTTP_201_CREATED)
def create_customer(
    payload: CustomerCreate,
    ctx: TenantContext = Depends(record_writer),
    db: Session = Depends(get_db),
) -> Customer:
    _require_discount_authority(ctx, payload.model_dump(exclude_unset=True))
    customer = create_scoped(db, ctx, Customer, **payload.model_dump())
    audit.created(db, ctx, customer, "customer")
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
    _require_discount_authority(ctx, payload.model_dump(exclude_unset=True), customer.default_discount_percent)
    audit.apply_audited_update(db, ctx, customer, "customer", payload.model_dump(exclude_unset=True))
    return customer


@router.delete("/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_customer(
    customer_id: uuid.UUID,
    ctx: TenantContext = Depends(record_writer),
    db: Session = Depends(get_db),
) -> Response:
    customer = get_scoped_or_404(db, ctx, Customer, customer_id)
    delete_or_409(db, customer, "Customer is referenced by other records", after_delete=audit.deletion(db, ctx, customer, "customer"))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
