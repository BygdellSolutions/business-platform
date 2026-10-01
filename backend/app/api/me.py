from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.tenant import TenantContext, get_tenant_context
from app.models import Organization
from app.schemas.me import MeOrganization, MeResponse, MeUser

router = APIRouter(prefix="/api", tags=["me"])


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
