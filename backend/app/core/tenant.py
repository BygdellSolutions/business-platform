import uuid
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.db import get_db
from app.models import OrganizationUser, Role, User

ORGANIZATION_HEADER = "X-Organization-Id"


@dataclass(frozen=True)
class TenantContext:
    """The authoritative tenant scope for a request.

    Every tenant-scoped query must take `organization_id` from here, never from
    request bodies, query strings or headers.
    """

    user: User
    organization_id: uuid.UUID
    role: Role


def get_tenant_context(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    x_organization_id: str | None = Header(default=None),
) -> TenantContext:
    """Resolve the active organization from the current user's memberships.

    X-Organization-Id is only a *selector* among organizations the user already
    belongs to. It grants nothing: it is looked up in the user's own memberships
    and rejected with 404 (as if the organization did not exist) if absent.
    """
    memberships = db.scalars(
        select(OrganizationUser)
        .where(OrganizationUser.user_id == user.id)
        .order_by(OrganizationUser.created_at, OrganizationUser.id)
    ).all()

    if x_organization_id is None:
        if not memberships:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No organization membership")
        if len(memberships) > 1:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"Multiple organizations: send the {ORGANIZATION_HEADER} header",
            )
        membership = memberships[0]
    else:
        try:
            selected = uuid.UUID(x_organization_id)
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail=f"Invalid {ORGANIZATION_HEADER} header"
            )
        membership = next((m for m in memberships if m.organization_id == selected), None)
        if membership is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Organization not found")

    return TenantContext(
        user=user, organization_id=membership.organization_id, role=Role(membership.role)
    )
