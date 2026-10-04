import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core import clock, memberships
from app.core.authz import roles_required
from app.core.db import get_db
from app.core.security_events import client_source
from app.core.tenant import TenantContext, get_tenant_context
from app.models import Role
from app.schemas.members import MemberRead, RoleChange

# Tenant-scoped: the organization is the active one (X-Organization-Id, resolved against the caller's memberships, a
# non-member gets the usual 404). Reading needs an owner or admin. The MUTATIONS take the tenant context only to
# learn the organization and who is acting; they never use its role: authority is decided inside the locked
# transaction from fresh rows (see app.core.memberships).
router = APIRouter(prefix="/api/members", tags=["members"])


def _forbidden(error: memberships.NotAllowed) -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": error.code, "message": error.message})


def _run(call):
    try:
        return call()
    except memberships.NotAMember:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Organization not found")
    except memberships.MemberNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Member not found")
    except memberships.NotAllowed as error:
        raise _forbidden(error)
    except memberships.LastOwner:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "last_owner", "message": "An organization must keep at least one owner."},
        )


@router.get("", response_model=list[MemberRead])
def read_members(ctx: TenantContext = Depends(roles_required(Role.OWNER, Role.ADMIN)), db: Session = Depends(get_db)) -> list[MemberRead]:
    return [MemberRead(**vars(member)) for member in memberships.list_members(db, ctx.organization_id, ctx.user.id)]


@router.post("/leave", status_code=status.HTTP_204_NO_CONTENT)
def leave_organization(request: Request, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)) -> Response:
    """Leave the active organization (any member; the last owner cannot)."""
    _run(lambda: memberships.leave(db, organization_id=ctx.organization_id, actor_user_id=ctx.user.id, now=clock.utcnow(), source=client_source(request)))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{membership_id}", response_model=MemberRead)
def change_member_role(
    membership_id: uuid.UUID, payload: RoleChange, request: Request, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)
) -> MemberRead:
    member = _run(
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
    _run(
        lambda: memberships.remove_member(
            db, organization_id=ctx.organization_id, actor_user_id=ctx.user.id, membership_id=membership_id, now=clock.utcnow(), source=client_source(request)
        )
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
