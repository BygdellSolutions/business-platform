import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock, memberships, ownership
from app.core.reauth import RecentAuthenticationFailed, confirm_recent_authentication
from app.core.authz import roles_required
from app.core.db import get_db
from app.core.passwords import AuthBusy
from app.core.security_events import client_source
from app.core.tenant import TenantContext, get_tenant_context
from app.core.throttle import Throttled
from app.models import OrganizationUser, Role, User
from app.schemas.members import Colleague, LeaveRequest, MemberRead, RoleChange

# Tenant-scoped: the organization is the active one (X-Organization-Id, resolved against the caller's memberships, a
# non-member gets the usual 404). Reading needs an owner or admin. The MUTATIONS take the tenant context only to
# learn the organization and who is acting; they never use its role: authority is decided inside the locked
# transaction from fresh rows (see app.core.memberships).
router = APIRouter(prefix="/api/members", tags=["members"])


def _forbidden(error: memberships.NotAllowed) -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": error.code, "message": error.message})


def require_recent_authentication(db: Session, ctx: TenantContext, password: str | None, request: Request) -> None:
    """The proof every destructive organization action needs (see app.core.reauth), as HTTP answers."""
    try:
        confirm_recent_authentication(db, user=ctx.user, password=password, source=client_source(request))
    except RecentAuthenticationFailed:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"code": "reauthentication_failed", "message": "Confirm with your password: it was missing or not correct."},
        )
    except Throttled as error:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "throttled", "message": "Too many attempts. Try again later."},
            headers={"Retry-After": str(error.retry_after)},
        )
    except AuthBusy:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": "busy", "message": "Busy. Try again in a moment."})


def run_membership_change(call):
    try:
        return call()
    except memberships.NotAMember:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Organization not found")
    except memberships.MemberNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Member not found")
    except memberships.NotAllowed as error:
        raise _forbidden(error)
    except ownership.OwnershipLimitReached:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "ownership_limit_reached", "message": "That member has reached their owned-organization limit and cannot become an owner here."},
        )
    except memberships.LastOwner:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "last_owner", "message": "An organization must keep at least one owner."},
        )


@router.get("", response_model=list[MemberRead])
def read_members(ctx: TenantContext = Depends(roles_required(Role.OWNER, Role.ADMIN)), db: Session = Depends(get_db)) -> list[MemberRead]:
    return [MemberRead(**vars(member)) for member in memberships.list_members(db, ctx.organization_id, ctx.user.id)]


@router.get("/people", response_model=list[Colleague])
def read_colleagues(ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)) -> list[Colleague]:
    """Every member's name, for any member (who performed a service). The full list stays owners' and admins'."""
    rows = db.execute(
        select(User.id, User.name)
        .join(OrganizationUser, OrganizationUser.user_id == User.id)
        .where(OrganizationUser.organization_id == ctx.organization_id)
        .order_by(User.name, User.id)
    ).all()
    return [Colleague(user_id=user_id, name=name) for user_id, name in rows]


@router.post("/leave", status_code=status.HTTP_204_NO_CONTENT)
def leave_organization(
    request: Request, payload: LeaveRequest | None = None, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)
) -> Response:
    """Leave the active organization (any member; the last owner cannot). Requires recent authentication."""
    require_recent_authentication(db, ctx, payload.password if payload else None, request)
    run_membership_change(lambda: memberships.leave(db, organization_id=ctx.organization_id, actor_user_id=ctx.user.id, now=clock.utcnow(), source=client_source(request)))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{membership_id}", response_model=MemberRead)
def change_member_role(
    membership_id: uuid.UUID, payload: RoleChange, request: Request, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)
) -> MemberRead:
    member = run_membership_change(
        lambda: memberships.change_role(
            db,
            organization_id=ctx.organization_id,
            actor_user_id=ctx.user.id,
            membership_id=membership_id,
            new_role=payload.role,
            now=clock.utcnow(),
            source=client_source(request),
        )
    )
    return MemberRead(**vars(member))


@router.delete("/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(membership_id: uuid.UUID, request: Request, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)) -> Response:
    run_membership_change(
        lambda: memberships.remove_member(
            db, organization_id=ctx.organization_id, actor_user_id=ctx.user.id, membership_id=membership_id, now=clock.utcnow(), source=client_source(request)
        )
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
