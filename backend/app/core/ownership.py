"""How many organizations an account may CREATE (decided 2026-10-12, replacing the owned-organization limit of 2026-10-08).

`users.max_owned_organizations` (the name is historical) is an account entitlement set only by the operator:
`bootstrap-user` gives 1, `set-owned-limit` any number, and every other account (an invited person) has 0. It counts
the organizations the account created that still exist (`organization_creation_requests`: one row per creation,
deleted with its organization). Becoming the owner of an organization that already exists (an owner invitation, a
promotion, a transfer) is not limited and does not use the allowance.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import OrganizationCreationRequest


def created_count(db: Session, user_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).select_from(OrganizationCreationRequest).where(OrganizationCreationRequest.user_id == user_id))
