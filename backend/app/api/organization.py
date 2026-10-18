import base64
import binascii
import hashlib

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.members import require_recent_authentication, run_membership_change
from datetime import timedelta

from fastapi.responses import StreamingResponse

from app.core import audit, clock, logo_image, memberships, rate_limit
from app.core.authz import roles_required
from app.core.currency import CURRENCY_LOCKED, default_currency_lock_reason
from app.core.config import settings
from app.core.db import get_db
from app.core.entity_registry import registry
from app.core.org_time import today_in
from app.core.organization_deletion import ConfirmationMismatch, delete_organization
from app.core.organization_export import archive_name, stream_export
from app.core.query import apply_update
from app.core.security_events import client_source
from app.core.tenant import TenantContext, get_tenant_context
from app.models import Organization, OrganizationLogo, Role
from app.schemas.members import OrganizationDeletion, OwnershipTransfer, RecentAuthentication
from app.schemas.organization import LogoRead, LogoUpload, OrganizationRead, OrganizationUpdate

router = APIRouter(prefix="/api/organization", tags=["organization"])

SETTINGS_ROLES = (Role.OWNER, Role.ADMIN)
EXPORT_VALID_FOR = timedelta(hours=24)  # how long a downloaded copy counts when the organization is deleted


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
        has_logo=organization.logo_id is not None,
        default_currency_locked=reason is not None,
        default_currency_lock_reason=reason,
        today=today_in(organization.timezone),
        recent_export=organization.last_export_at is not None and clock.utcnow() - organization.last_export_at <= EXPORT_VALID_FOR,
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


# --- the logo: printed on the organization's PDFs; changing it is the owner's decision -----------------------------------


def _current_logo(db: Session, organization: Organization) -> OrganizationLogo | None:
    if organization.logo_id is None:
        return None
    return db.scalar(select(OrganizationLogo).where(OrganizationLogo.organization_id == organization.id, OrganizationLogo.id == organization.logo_id))


@router.get("/logo", response_model=LogoRead)
def read_logo(ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)) -> LogoRead:
    logo = _current_logo(db, _active_organization(db, ctx))
    if logo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    return LogoRead(data_base64=base64.b64encode(logo.content).decode("ascii"), width=logo.width, height=logo.height)


@router.post("/logo", response_model=OrganizationRead)
def upload_logo(
    payload: LogoUpload, ctx: TenantContext = Depends(roles_required(Role.OWNER)), db: Session = Depends(get_db)
) -> OrganizationRead:
    try:
        raw = base64.b64decode(payload.data_base64, validate=True)
        picture = logo_image.normalize(raw)
    except (binascii.Error, ValueError):
        raise _invalid_logo("The file could not be read.") from None
    except logo_image.InvalidLogo as error:
        raise _invalid_logo(error.message) from None
    # Row lock: an invoice issued at the same moment records either the old or the new version, never a mix.
    organization = _active_organization(db, ctx, for_update=True)
    logo = OrganizationLogo(
        organization_id=organization.id,
        content=picture.content,
        sha256=hashlib.sha256(picture.content).hexdigest(),
        width=picture.width,
        height=picture.height,
        byte_size=len(picture.content),
        created_by=ctx.user.id,
    )
    db.add(logo)
    db.flush()
    previous = organization.logo_id
    organization.logo_id = logo.id
    audit.record(
        db, ctx, entity_type="organization", entity_id=organization.id, action="logo_changed",
        changes={"logo_id": {"from": str(previous) if previous else None, "to": str(logo.id)}},
    )
    db.commit()  # the session is not committed for us: without this the new version and the audit event are rolled back when the request ends
    db.refresh(organization)
    return read_organization_profile(db, organization)


@router.delete("/logo", status_code=status.HTTP_204_NO_CONTENT)
def remove_logo(ctx: TenantContext = Depends(roles_required(Role.OWNER)), db: Session = Depends(get_db)) -> Response:
    organization = _active_organization(db, ctx, for_update=True)
    if organization.logo_id is not None:
        audit.record(
            db, ctx, entity_type="organization", entity_id=organization.id, action="logo_removed",
            changes={"logo_id": {"from": str(organization.logo_id), "to": None}},
        )
        organization.logo_id = None  # the version stays: issued documents may refer to it
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _invalid_logo(message: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"code": "invalid_logo", "message": message})


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



@router.post("/export", response_class=StreamingResponse, responses={200: {"content": {"application/zip": {}}, "description": "A complete copy of the organization's data"}})
def export_organization_data(
    payload: RecentAuthentication,
    request: Request,
    ctx: TenantContext = Depends(roles_required(Role.OWNER)),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """A complete copy of the organization's data as a ZIP (owner only, recent authentication, a few per hour). Streamed: nothing
    is built in memory, and the organization counts as exported only when the whole archive has been sent."""
    require_recent_authentication(db, ctx, payload.password, request)
    rate_limit.export_copy(ctx)
    registry.call_hooks("organization.prepare_export", db, ctx, limit=settings.export_render_limit)  # modules freeze documents that have no stored PDF yet
    organization = _active_organization(db, ctx)
    now = clock.utcnow()
    name, organization_id = organization.name, organization.id
    db.commit()  # end (only read) this request's own transaction, so its connection is not held idle for the whole download
    return StreamingResponse(
        stream_export(organization_id=organization_id, organization_name=name, now=now),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{archive_name(name, now)}"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/delete", status_code=status.HTTP_204_NO_CONTENT)
def delete_this_organization(
    payload: OrganizationDeletion, request: Request, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)
) -> Response:
    """Delete the organization and ALL its data for good (owner only, recent authentication, the name typed). Nothing is kept, so the
    owner must either have downloaded a complete copy in the last day or say explicitly that they want none."""
    require_recent_authentication(db, ctx, payload.password, request)
    if not payload.without_copy:
        exported = db.scalar(select(Organization.last_export_at).where(Organization.id == ctx.organization_id))
        if exported is None or clock.utcnow() - exported > EXPORT_VALID_FOR:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={"code": "export_required", "message": "Download a copy of your data first, or choose to delete without a copy."},
            )
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
