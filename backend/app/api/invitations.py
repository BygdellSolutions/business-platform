import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.api.auth import _busy, _issued, session_mode_only
from app.core import clock, invitations, memberships, passwords
from app.core.auth import get_current_user
from app.core.db import get_db
from app.core.passwords import AuthBusy, admission
from app.core.security_events import client_source
from app.core.tenant import TenantContext, get_tenant_context
from app.models import User
from app.schemas.auth import SessionResponse
from app.schemas.invitations import (
    AcceptedResponse,
    AcceptNewBody,
    InvitationCreate,
    InvitationCreated,
    InvitationPreview,
    InvitationRead,
    TokenBody,
)

# Administration is tenant-scoped (X-Organization-Id is resolved against the caller's memberships; a non-member gets
# the usual 404). The endpoints use the tenant context only to learn the organization and who is acting; authority is
# decided inside the locked transaction from fresh membership rows (see app.core.invitations).
router = APIRouter(prefix="/api/invitations", tags=["invitations"])

# The invitee is NOT organization-scoped: the bearer token (in a POST body) locates the invitation, and the
# organization and role come from that locked row.
public_router = APIRouter(prefix="/api/invite", tags=["invitations"])

UNUSABLE = "Invitation not found"


def _run(call):
    try:
        return call()
    except memberships.NotAMember:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Organization not found")
    except invitations.InvitationNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=UNUSABLE)
    except memberships.NotAllowed as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": error.code, "message": error.message})
    except invitations.AlreadyMember:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "already_member", "message": "That person already belongs to this organization."})
    except invitations.PendingExists:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "invitation_pending", "message": "There is already a pending invitation for that email. Regenerate it to replace it."})
    except invitations.InvitationAccepted:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "invitation_accepted", "message": "This invitation was already accepted."})
    except invitations.InvitationNotPending:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "invitation_not_pending", "message": "This invitation is no longer pending."})


def _read(invitation, now) -> dict:
    return dict(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        created_at=invitation.created_at,
        expires_at=invitation.expires_at,
        state=invitations.state_of(invitation, now),
    )


@router.get("", response_model=list[InvitationRead])
def list_invitations(ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)) -> list[InvitationRead]:
    """Pending and expired-but-unsuperseded invitations. Owner/admin only. Never a token."""
    if ctx.role not in memberships.ADMINISTRATORS:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "membership_admin_forbidden", "message": "Only an owner or admin can manage invitations."})
    now = clock.utcnow()
    return [InvitationRead(**_read(i, now)) for i in invitations.list_pending(db, ctx.organization_id)]


@router.post("", response_model=InvitationCreated, status_code=status.HTTP_201_CREATED)
def create_invitation(
    payload: InvitationCreate, request: Request, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)
) -> InvitationCreated:
    now = clock.utcnow()
    created = _run(
        lambda: invitations.create(
            db, organization_id=ctx.organization_id, actor_user_id=ctx.user.id, email=payload.email, role=payload.role, now=now, source=client_source(request)
        )
    )
    return InvitationCreated(**_read(created.invitation, now), token=created.token)


@router.post("/{invitation_id}/regenerate", response_model=InvitationCreated, status_code=status.HTTP_201_CREATED)
def regenerate_invitation(invitation_id: uuid.UUID, request: Request, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)) -> InvitationCreated:
    now = clock.utcnow()
    created = _run(
        lambda: invitations.regenerate(
            db, organization_id=ctx.organization_id, actor_user_id=ctx.user.id, invitation_id=invitation_id, now=now, source=client_source(request)
        )
    )
    return InvitationCreated(**_read(created.invitation, now), token=created.token)


@router.delete("/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invitation(invitation_id: uuid.UUID, request: Request, ctx: TenantContext = Depends(get_tenant_context), db: Session = Depends(get_db)) -> Response:
    _run(
        lambda: invitations.revoke(
            db, organization_id=ctx.organization_id, actor_user_id=ctx.user.id, invitation_id=invitation_id, now=clock.utcnow(), source=client_source(request)
        )
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- the invitee ----------------------------------------------------------------------------------------------------------------------


@public_router.post("/preview", response_model=InvitationPreview)
def preview_invitation(payload: TokenBody, db: Session = Depends(get_db)) -> InvitationPreview:
    """Pre-authentication (the BFF applies the pre-auth double submit). A POST because the secret must not be in a URL."""
    result = _run(lambda: invitations.preview(db, payload.token, clock.utcnow()))
    return InvitationPreview(organization_name=result.organization_name, email=result.email, role=result.role, account_exists=result.account_exists)


@public_router.post("/accept", response_model=AcceptedResponse)
def accept_invitation(payload: TokenBody, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> AcceptedResponse:
    """An authenticated account accepts (ordinary session CSRF). It must be the account the invitation was made for."""
    try:
        accepted = invitations.accept_existing(db, user=user, token=payload.token, now=clock.utcnow(), source=client_source(request))
    except invitations.WrongAccount:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "invitation_wrong_account", "message": "This invitation was made for a different account."})
    except invitations.InvitationNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=UNUSABLE)
    return AcceptedResponse(organization_id=accepted.organization_id, role=accepted.role, joined=accepted.joined)


class AcceptedSession(SessionResponse):
    organization_id: uuid.UUID
    role: str


@public_router.post("/accept-new", response_model=AcceptedSession, dependencies=[Depends(session_mode_only)])
def accept_invitation_as_new_account(payload: AcceptNewBody, request: Request, db: Session = Depends(get_db)) -> AcceptedSession:
    """Pre-authentication, session mode only: create the invited account (name and password), join, and sign in.

    Nothing is written before every check has passed: the password policy and the (bounded) password hashing happen
    first and cost nothing for an unusable token; then ONE transaction creates user, credential, membership, session
    and settles the invitation. A weak password or any failure consumes nothing.
    """
    now = clock.utcnow()
    try:
        email = invitations.usable_email(db, payload.token, now)
        problem = passwords.policy_problem(payload.password, email)
        if problem is not None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"code": "password_policy", "message": problem})
        with admission.slot():
            password_hash = passwords.hash_password(payload.password)
        issued, accepted = invitations.accept_new(
            db, token=payload.token, name=payload.name, password_hash=password_hash, now=now, source=client_source(request), user_agent=request.headers.get("user-agent")
        )
    except invitations.InvitationNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=UNUSABLE)
    except invitations.AccountExists:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "account_exists", "message": "An account with this email already exists. Sign in to accept the invitation."})
    except AuthBusy:
        raise _busy()
    base = _issued(issued)
    return AcceptedSession(**base.model_dump(), organization_id=accepted.organization_id, role=accepted.role)
