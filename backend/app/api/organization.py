from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.members import require_recent_authentication, run_membership_change
from app.core import clock, memberships
from app.core.authz import roles_required
from app.core.currency import CURRENCY_LOCKED, default_currency_lock_reason
from app.core.db import get_db
from app.core.org_time import today_in
from app.core.organization_deletion import ConfirmationMismatch, delete_organization
from app.core.query import apply_update
from app.core.security_events import client_source
from app.core.tenant import TenantContext, get_tenant_context
from app.models import Organization, Role
from app.schemas.members import OrganizationDeletion, OwnershipTransfer
from app.schemas.organization import OrganizationRead, OrganizationUpdate

router = APIRouter(prefix="/api/organization", tags=["organization"])

SETTINGS_ROLES = (Role.OWNER, Role.ADMIN)


def read_organization_profile(db: Session, organization: Organization) -> OrganizationRead:
    reason = None
    if organization.default_currency is not None:
        reason = default_currency_lock_reason(db, organization.id)
    return OrganizationRead(
        **{
            name: getattr(organization, name)
            for name in OrganizationRead.model_fields
            if hasattr(organization, name)
        },
        default_currency_locked=reason is not None,
        default_currency_lock_reason=reason,
        today=today_in(organization.timezone),
    )


def _active_organization(db: Session, ctx: TenantContext, *, for_update: bool = False) -> Organization:
    query = select(Organization).where(Organization.id == ctx.organization_id)
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    return db.execute(query).scalar_one()


@router.get("", response_model=OrganizationRead)
def read_organization(
    ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)
) -> OrganizationRead:
    """The active organization's settings. Any member may read them: the currency and the
    business profile are shown to everyone who works in the organization."""
    return read_organization_profile(db, _active_organization(db, ctx))


@router.patch("", response_model=OrganizationRead)
def update_organization(
    payload: OrganizationUpdate,
    ctx: TenantContext = Depends(roles_required(*SETTINGS_ROLES)),
    db: Session = Depends(get_db),
) -> OrganizationRead:
    # Row lock: creators of prices (items, transactions) hold it FOR SHARE, so the check
    # below sees every record created before this change and none can be created during it.
    organization = _active_organization(db, ctx, for_update=True)
    values = payload.model_dump(exclude_unset=True)
    new_currency = values.get("default_currency")
    if (
        new_currency is not None
        and organization.default_currency is not None
        and new_currency != organization.default_currency
    ):
        reason = default_currency_lock_reason(db, organization.id)
        if reason is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "code": CURRENCY_LOCKED,
                    "message": f"The default currency can no longer be changed. {reason}",
                },
            )
    apply_update(db, organization, values)
    return read_organization_profile(db, organization)


# --- the danger zone: decided from fresh, locked membership rows, never from the request's role --------------------


@router.post("/transfer-ownership", status_code=status.HTTP_204_NO_CONTENT)
def transfer_ownership(
    payload: OwnershipTransfer, request: Request, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)
) -> Response:
    """An owner makes another member the owner and becomes an admin. Requires recent authentication."""
    require_recent_authentication(db, ctx, payload.password, request)
    run_membership_change(
        lambda: memberships.transfer_ownership(
            db, organization_id=ctx.organization_id, actor_user_id=ctx.user.id, membership_id=payload.membership_id,
            now=clock.utcnow(), source=client_source(request),
        )
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/delete", status_code=status.HTTP_204_NO_CONTENT)
def delete_this_organization(
    payload: OrganizationDeletion, request: Request, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)
) -> Response:
    """Delete the organization and ALL its data for good (owner only, recent authentication, the name typed)."""
    require_recent_authentication(db, ctx, payload.password, request)
    try:
        run_membership_change(
            lambda: delete_organization(
                db, organization_id=ctx.organization_id, actor_user_id=ctx.user.id, confirm_name=payload.confirm_name,
                now=clock.utcnow(), source=client_source(request),
            )
        )
    except ConfirmationMismatch:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[{"loc": ["body", "confirm_name"], "msg": "Type the organization's name exactly as shown", "type": "confirmation_mismatch"}],
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
