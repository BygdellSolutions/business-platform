from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from fastapi.responses import JSONResponse

from app.core import account_data, mfa_policy, mfa_service, ownership, rate_limit
from app.core.auth import get_current_user
from app.core.db import get_db
from app.core.tenant import TenantContext, get_tenant_context
from app.models import Organization, OrganizationUser, User
from app.schemas.me import MeOrganization, MeResponse, MeUser, MyOrganization, MyUser

router = APIRouter(prefix="/api", tags=["me"])


@router.get("/me/user", response_model=MyUser)
def read_my_user(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> MyUser:
    """Who the session is: the user's own identity, with no organization context. Needs authentication only."""
    created = ownership.created_count(db, user.id)
    return MyUser(
        id=user.id, email=user.email, name=user.name, can_create_organizations=created < user.max_owned_organizations,
        created_organizations=created, max_owned_organizations=user.max_owned_organizations,
        mfa_enabled=mfa_service.is_enabled(db, user.id), mfa_required=mfa_policy.required_for_user(db, user),
    )


@router.get("/me/export")
def export_my_account(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> JSONResponse:
    """A copy of everything the platform holds about the caller's own ACCOUNT (profile, memberships, sessions, security events, the state
    of the second factor; never a secret). The business records of an organization belong to that organization."""
    rate_limit.enforce("export", f"u:{user.id}", 10, window=3600.0)
    return JSONResponse(
        account_data.export_account(db, user),
        headers={"Content-Disposition": 'attachment; filename="account-data.json"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


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
