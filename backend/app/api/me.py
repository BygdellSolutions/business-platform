from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.db import get_db
from app.core.tenant import TenantContext, get_tenant_context
from app.models import Organization, OrganizationUser, User
from app.schemas.me import MeOrganization, MeResponse, MeUser, MyOrganization

router = APIRouter(prefix="/api", tags=["me"])


@router.get("/me/organizations", response_model=list[MyOrganization])
def list_my_organizations(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[MyOrganization]:
    """The organizations the CURRENT USER belongs to, so a client can offer to switch.

    Needs authentication but no active organization (it is how a client finds one). It
    answers only from the callers own memberships and exposes nothing else about any
    organization, so it cannot be used to discover other tenants.
    """
    rows = db.execute(
        select(Organization.id, Organization.name, OrganizationUser.role)
        .join(OrganizationUser, OrganizationUser.organization_id == Organization.id)
        .where(OrganizationUser.user_id == user.id)
        .order_by(Organization.name, Organization.id)
    )
    return [MyOrganization(id=row.id, name=row.name, role=row.role) for row in rows]


@router.get("/me", response_model=MeResponse)
def read_me(
    ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)
) -> MeResponse:
    organization = db.get(Organization, ctx.organization_id)
    if organization is None:  # cannot happen while the membership FK holds
        raise HTTPException(status_code=404, detail="Organization not found")
    return MeResponse(
        user=MeUser(id=ctx.user.id, email=ctx.user.email, name=ctx.user.name),
        organization=MeOrganization(id=organization.id, name=organization.name),
        role=ctx.role,
    )
