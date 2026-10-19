import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.api.deps import Pagination, Sorting, pagination, sorted_by, sorting
from app.core import audit
from app.core.authz import record_writer
from app.core.db import get_db
from app.core.query import commit_and_refresh, contains_pattern, delete_or_409, number_matches
from app.core.tenant import TenantContext, get_tenant_context
from app.core.tenant_scope import create_scoped, get_scoped_or_404, scoped_select
from app.models import Supplier
from app.schemas.supplier import SupplierCreate, SupplierRead, SupplierUpdate

router = APIRouter(prefix="/api/suppliers", tags=["suppliers"])

# The columns the list sorts by (?sort=...&dir=...); names compare without case.
SUPPLIER_SORTS = {
    "number": Supplier.number, "name": func.lower(Supplier.name), "contact_person": func.lower(Supplier.contact_person), "email": func.lower(Supplier.email), "phone": Supplier.phone, "active": Supplier.active,
}


@router.post("", response_model=SupplierRead, status_code=status.HTTP_201_CREATED)
def create_supplier(payload: SupplierCreate, ctx: TenantContext = Depends(record_writer), db: Session = Depends(get_db)) -> Supplier:
    supplier = create_scoped(db, ctx, Supplier, **payload.model_dump())
    audit.created(db, ctx, supplier, "supplier")
    commit_and_refresh(db, supplier)
    return supplier


@router.get("", response_model=list[SupplierRead])
def list_suppliers(
    q: str | None = Query(default=None, max_length=255, description="Name, contact person or email contains"),
    active: bool | None = None,
    page: Pagination = Depends(pagination),
    sort: Sorting = Depends(sorting),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[Supplier]:
    query = scoped_select(Supplier, ctx)
    if q:
        pattern = contains_pattern(q)
        query = query.where(
            or_(
                Supplier.name.ilike(pattern, escape="\\"),
                Supplier.contact_person.ilike(pattern, escape="\\"),
                Supplier.email.ilike(pattern, escape="\\"),
                number_matches(Supplier.number, q),
            )
        )
    if active is not None:
        query = query.where(Supplier.active == active)
    query = sorted_by(query, sort, SUPPLIER_SORTS, (Supplier.name, Supplier.id)).limit(page.limit).offset(page.offset)
    return list(db.scalars(query))


@router.get("/{supplier_id}", response_model=SupplierRead)
def read_supplier(supplier_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)) -> Supplier:
    return get_scoped_or_404(db, ctx, Supplier, supplier_id)


@router.patch("/{supplier_id}", response_model=SupplierRead)
def update_supplier(
    supplier_id: uuid.UUID, payload: SupplierUpdate, ctx: TenantContext = Depends(record_writer), db: Session = Depends(get_db)
) -> Supplier:
    supplier = get_scoped_or_404(db, ctx, Supplier, supplier_id)
    audit.apply_audited_update(db, ctx, supplier, "supplier", payload.model_dump(exclude_unset=True))
    return supplier


@router.delete("/{supplier_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_supplier(supplier_id: uuid.UUID, ctx: TenantContext = Depends(record_writer), db: Session = Depends(get_db)) -> Response:
    """A supplier named on a delivery cannot be deleted (409): deactivate it instead."""
    supplier = get_scoped_or_404(db, ctx, Supplier, supplier_id)
    delete_or_409(db, supplier, "Supplier is referenced by other records", after_delete=audit.deletion(db, ctx, supplier, "supplier"))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
