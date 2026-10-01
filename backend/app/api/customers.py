import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.tenant import TenantContext, get_tenant_context
from app.core.tenant_scope import create_scoped, get_scoped_or_404, scoped_select
from app.models import Customer
from app.schemas.customer import CustomerCreate, CustomerRead, CustomerUpdate

router = APIRouter(prefix="/api/customers", tags=["customers"])


def _contains_pattern(text: str) -> str:
    """LIKE pattern matching `text` literally (so '%' and '_' are not wildcards)."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@router.post("", response_model=CustomerRead, status_code=status.HTTP_201_CREATED)
def create_customer(
    payload: CustomerCreate,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> Customer:
    customer = create_scoped(db, ctx, Customer, **payload.model_dump())
    db.refresh(customer)
    db.commit()
    return customer


@router.get("", response_model=list[CustomerRead])
def list_customers(
    q: str | None = Query(default=None, max_length=255, description="Name or email contains"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[Customer]:
    query = scoped_select(Customer, ctx)
    if q:
        pattern = _contains_pattern(q)
        query = query.where(
            or_(
                Customer.name.ilike(pattern, escape="\\"),
                Customer.email.ilike(pattern, escape="\\"),
            )
        )
    query = query.order_by(Customer.name, Customer.id).limit(limit).offset(offset)
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
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> Customer:
    customer = get_scoped_or_404(db, ctx, Customer, customer_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(customer, field, value)
    db.flush()
    db.refresh(customer)
    db.commit()
    return customer


@router.delete("/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_customer(
    customer_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> Response:
    customer = get_scoped_or_404(db, ctx, Customer, customer_id)
    db.delete(customer)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
