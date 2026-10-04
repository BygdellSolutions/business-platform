import re

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.api.organization import read_organization_profile
from app.core import clock, organizations, security_events
from app.core.auth import get_current_user
from app.core.db import get_db
from app.models import User
from app.schemas.organization import OrganizationCreate, OrganizationRead

# Deliberately NOT tenant-scoped: there is no organization yet. Authentication (`get_current_user`, which also
# enforces CSRF in session mode) identifies the user; `users.can_create_organizations` decides; nothing in the
# request can name an owner, a role or an organization.
router = APIRouter(prefix="/api/organizations", tags=["organizations"])

REQUEST_KEY = re.compile(r"^[A-Za-z0-9_-]{43}$")


@router.post("", response_model=OrganizationRead, status_code=status.HTTP_201_CREATED)
def create_organization(
    payload: OrganizationCreate,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None),
) -> OrganizationRead:
    """Create an organization; the authenticated user becomes its owner, in one transaction.

    201 with the new organization; 200 with the SAME organization when an `Idempotency-Key` is repeated with the
    same body (a retry after a lost response). 403 when the account may not create organizations, 409 when the
    key was used with a different body, 422 for an invalid body or key. The organization is then reachable
    like any other: through the user's memberships and `/o/{id}`.
    """
    if idempotency_key is not None and not REQUEST_KEY.fullmatch(idempotency_key):
        raise HTTPException(422, detail="Idempotency-Key must be 43 URL-safe base64 characters")
    try:
        created = organizations.create_organization(
            db, user, payload.model_dump(), request_key=idempotency_key, now=clock.utcnow(), source=security_events.client_source(request)
        )
    except organizations.UserNotActive:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    except organizations.CreationNotAllowed:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"code": "organization_creation_not_allowed", "message": "This account may not create organizations."},
        )
    except organizations.RequestKeyConflict:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "request_key_conflict", "message": "This request key was already used for a different request."},
        )
    if created.replayed:
        response.status_code = status.HTTP_200_OK
    return read_organization_profile(db, created.organization)
