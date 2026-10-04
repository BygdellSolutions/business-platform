"""Organization creation: one authenticated user creates a tenant and atomically becomes its owner.

This is the one business operation that is NOT tenant-scoped (there is no organization yet), so it has its own
rules, all enforced here and not at the edge:

  * Who may create: the authenticated user, and only if `users.can_create_organizations` is true. The flag is a
    property of the ACCOUNT (set by an operator); no membership role implies it.
  * The owner is the authenticated user. Nothing in the request names an owner, a role or an organization id.
  * One transaction: the organization row, the owner membership, the retry record and the security event are
    inserted inside one savepoint and committed once. If anything fails nothing remains; a bare organization
    without its owner can never be committed. There is no compensating deletion and no background task.
  * The user row is locked FOR UPDATE and re-read from the database (not taken from the identity map) before the
    flag is judged. That makes the permission check fresh and serializes two creations by the same user
    (and a capability change by the operator) without any other coordination. Lock order is always
    users -> (organizations, organization_users, organization_creation_requests); nothing locks a user while
    holding an organization row, so the order cannot invert.
  * Retries: a client-generated `Idempotency-Key` (scoped to the creator) with the request body's hash. The same
    key with the same body returns the organization created the first time (only while the user is still a
    member of it); the same key with a different body is refused. Without a key nothing is deduplicated.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import auth_service
from app.models import Organization, OrganizationUser, Role, SecurityEvent, User
from app.models.organization_request import OrganizationCreationRequest


class CreationNotAllowed(Exception):
    """The user may not create organizations (the flag is false)."""


class UserNotActive(Exception):
    """The user no longer exists or was disabled while the request was in flight."""


class RequestKeyConflict(Exception):
    """The idempotency key was used before with a different body, or its organization is not visible to the user."""


@dataclass(frozen=True)
class Created:
    organization: Organization
    replayed: bool


def request_hash(values: dict) -> str:
    """The hash of the validated request values (the API layer validates; this layer only fingerprints)."""
    canonical = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _between_inserts() -> None:
    """A seam for fault-injection tests: it runs after the organization insert and before the owner membership."""


def create_organization(
    db: Session,
    user: User,
    values: dict,
    *,
    request_key: str | None,
    now: datetime,
    source: str | None,
) -> Created:
    locked = auth_service.lock_user(db, user.id)  # fresh read of the row, and the lock
    if locked is None or not locked.is_active:
        raise UserNotActive()
    if not locked.can_create_organizations:
        raise CreationNotAllowed()

    digest = request_hash(values)
    if request_key is not None:
        previous = db.get(OrganizationCreationRequest, (locked.id, request_key))
        if previous is not None:
            if previous.request_hash != digest:
                raise RequestKeyConflict()
            # Replay only through a CURRENT membership: the original organization is returned to a user who still
            # belongs to it, and to nobody else. Role does not matter (it may have changed since).
            organization = db.scalar(
                select(Organization)
                .join(OrganizationUser, OrganizationUser.organization_id == Organization.id)
                .where(Organization.id == previous.organization_id, OrganizationUser.user_id == locked.id)
            )
            if organization is None:
                raise RequestKeyConflict()
            return Created(organization, replayed=True)

    with db.begin_nested():
        organization = Organization(**values)
        db.add(organization)
        db.flush()
        _between_inserts()
        db.add(OrganizationUser(organization_id=organization.id, user_id=locked.id, role=Role.OWNER))
        if request_key is not None:
            db.add(OrganizationCreationRequest(user_id=locked.id, request_key=request_key, request_hash=digest, organization_id=organization.id))
        # Written here (not through `security_events.record`, which stays free of tenant concepts): who, which new
        # organization, when and from where. Never the form's contents.
        db.add(
            SecurityEvent(
                occurred_at=now,
                event_type="organization_created",
                actor_user_id=locked.id,
                organization_id=organization.id,
                source=source,
                detail="keyed" if request_key is not None else "unkeyed",
            )
        )
        db.flush()
    db.commit()
    return Created(organization, replayed=False)

