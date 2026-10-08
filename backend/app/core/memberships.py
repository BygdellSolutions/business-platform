"""Membership administration: list, change role, remove, leave (and the operator's owner repair).

Roles: owner, admin, accountant, employee, viewer. Authority (decided HERE, never in the frontend):

    OWNER     may change other members to any role and remove other members, promote to owner, demote another owner;
              may demote THEMSELVES, or leave, only while another owner remains
    ADMIN     may manage only members whose CURRENT role is accountant, employee or viewer, and only to one of those;
              may remove only such members; may never touch an owner or another admin or grant admin/owner
    others    no membership administration (they may still leave)
    SELF      nobody changes their own role except an owner demoting themselves; every member may leave; the last
              owner may not. Removing yourself through the administrative removal is refused (leave is explicit).

Fresh authority under lock (the contract of every mutation here)
----------------------------------------------------------------
Nothing is authorized from the tenant context captured earlier in the request, from the frontend, or from an ORM
object loaded earlier. Each mutation first locks the organization's membership rows and re-reads them from the
database, then decides:

  LOCK ORDER: ONE statement, `SELECT ... FROM organization_users WHERE organization_id = :org ORDER BY id FOR UPDATE`
  with `populate_existing`. That locks the actor row, the target row and every owner row (they are all rows of this
  organization) in ascending `id` order, so every membership mutation of one organization acquires the same locks in
  the same order: they serialize, and cannot deadlock with each other. Rows of other organizations are never selected,
  counted or locked. Organizations have a handful of members, so locking the set is cheap and makes "who else is an
  owner" exact. (The creation of an organization inserts rows and takes no such lock; it cannot reduce owners.)

After the lock: re-check the actor's membership and CURRENT role, the target's membership and CURRENT role, the
requested resulting role, and (only now) the owner count. The deferred database trigger
`organization_users_owner_required` is a backstop for writers that bypass this code; it is not the first line.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import ownership
from app.models import Organization, OrganizationUser, Role, SecurityEvent, User

ADMIN_MANAGEABLE = frozenset({Role.ACCOUNTANT, Role.EMPLOYEE, Role.VIEWER})
ADMINISTRATORS = frozenset({Role.OWNER, Role.ADMIN})

OWNER_CONSTRAINT = "organization_owner_required"


class NotAMember(Exception):
    """The actor has no membership in the organization (any more): the organization looks like it does not exist."""


class MemberNotFound(Exception):
    """No such membership in THIS organization (a random id and another organization's id look the same)."""


class NotAllowed(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class LastOwner(Exception):
    """The operation would leave the organization without an owner (one error for demotion, removal and leave)."""


class RepairNotNeeded(Exception):
    """The organization already has an owner: the operator repair is only for an organization without one."""


@dataclass(frozen=True)
class Member:
    id: uuid.UUID
    name: str
    email: str
    role: Role
    is_you: bool


def _lock_members(db: Session, organization_id: uuid.UUID) -> list[OrganizationUser]:
    """Lock and freshly re-read every membership of ONE organization, in ascending id order (see the module docstring)."""
    return list(
        db.scalars(
            select(OrganizationUser)
            .where(OrganizationUser.organization_id == organization_id)
            .order_by(OrganizationUser.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


def lock_members(db: Session, organization_id: uuid.UUID) -> list[OrganizationUser]:
    """The S4 lock, for other tenant-aware services (invitations): ALL membership rows of one organization, ascending id."""
    return _lock_members(db, organization_id)


def administrator(rows: list[OrganizationUser], user_id: uuid.UUID) -> OrganizationUser:
    """The acting user's CURRENT membership among freshly locked rows, which must be an owner or admin."""
    actor = _actor(rows, user_id)
    _require_administrator(actor)
    return actor


def _owners(rows: list[OrganizationUser]) -> int:
    return sum(1 for row in rows if row.role == Role.OWNER)


def _actor(rows: list[OrganizationUser], user_id: uuid.UUID) -> OrganizationUser:
    actor = next((row for row in rows if row.user_id == user_id), None)
    if actor is None:
        raise NotAMember()
    return actor


def _target(rows: list[OrganizationUser], membership_id: uuid.UUID) -> OrganizationUser:
    target = next((row for row in rows if row.id == membership_id), None)
    if target is None:
        raise MemberNotFound()
    return target


def _require_administrator(actor: OrganizationUser) -> None:
    if Role(actor.role) not in ADMINISTRATORS:
        raise NotAllowed("membership_admin_forbidden", "Only an owner or admin can manage members.")


def _commit(db: Session) -> None:
    """The commit is where the deferred owner-loss trigger runs; if it ever refuses, that is the same conflict."""
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if OWNER_CONSTRAINT in str(error.orig):
            raise LastOwner() from error
        if ownership.is_limit_violation(error):
            raise ownership.OwnershipLimitReached() from error
        raise


def _event(db: Session, event_type: str, now: datetime, actor: OrganizationUser, detail: str, source: str | None) -> None:
    # Ids and roles only: never an email, a name or a request body. The target user id and the roles are in `detail`.
    db.add(SecurityEvent(occurred_at=now, event_type=event_type, actor_user_id=actor.user_id, organization_id=actor.organization_id, source=source, detail=detail))


def list_members(db: Session, organization_id: uuid.UUID, viewer_user_id: uuid.UUID) -> list[Member]:
    rows = db.execute(
        select(OrganizationUser.id, User.name, User.email, OrganizationUser.role, OrganizationUser.user_id)
        .join(User, User.id == OrganizationUser.user_id)
        .where(OrganizationUser.organization_id == organization_id)
        .order_by(User.name, User.email, OrganizationUser.id)
    )
    return [Member(r.id, r.name, r.email, Role(r.role), r.user_id == viewer_user_id) for r in rows]


def _member(db: Session, row: OrganizationUser, viewer_user_id: uuid.UUID) -> Member:
    user = db.get(User, row.user_id)
    return Member(row.id, user.name, user.email, Role(row.role), row.user_id == viewer_user_id)


def change_role(db: Session, *, organization_id: uuid.UUID, actor_user_id: uuid.UUID, membership_id: uuid.UUID, new_role: Role, now: datetime, source: str | None) -> Member:
    rows = _lock_members(db, organization_id)
    actor = _actor(rows, actor_user_id)
    _require_administrator(actor)
    target = _target(rows, membership_id)
    old_role, actor_role = Role(target.role), Role(actor.role)

    if target.id == actor.id:
        # Only an owner may change their own role, and only to step DOWN (a no-op stays a no-op).
        if actor_role != Role.OWNER:
            raise NotAllowed("self_role_change_not_allowed", "You cannot change your own role.")
    elif actor_role == Role.ADMIN and (old_role not in ADMIN_MANAGEABLE or new_role not in ADMIN_MANAGEABLE):
        raise NotAllowed("insufficient_authority", "An admin can only manage accountants, employees and viewers.")

    if new_role != old_role:
        if old_role == Role.OWNER and _owners(rows) <= 1:  # counted only now, after the locks
            raise LastOwner()
        if new_role == Role.OWNER:
            ownership.ensure_can_own_another(db, target.user_id)  # becoming an owner counts against the person's limit
        target.role = new_role
        db.flush()
        _event(db, "member_role_changed", now, actor, f"{target.user_id} {old_role}>{new_role}", source)
    member = _member(db, target, actor_user_id)
    _commit(db)
    return member


def remove_member(db: Session, *, organization_id: uuid.UUID, actor_user_id: uuid.UUID, membership_id: uuid.UUID, now: datetime, source: str | None) -> None:
    rows = _lock_members(db, organization_id)
    actor = _actor(rows, actor_user_id)
    _require_administrator(actor)
    target = _target(rows, membership_id)
    old_role = Role(target.role)

    if target.id == actor.id:
        raise NotAllowed("self_removal_use_leave", "Use Leave organization to remove yourself.")
    if Role(actor.role) == Role.ADMIN and old_role not in ADMIN_MANAGEABLE:
        raise NotAllowed("insufficient_authority", "An admin can only manage accountants, employees and viewers.")
    if old_role == Role.OWNER and _owners(rows) <= 1:
        raise LastOwner()

    target_user_id = target.user_id
    db.delete(target)  # the membership only: the user, their sessions and their other memberships are untouched
    db.flush()
    _event(db, "member_removed", now, actor, f"{target_user_id} {old_role}", source)
    _commit(db)


def leave(db: Session, *, organization_id: uuid.UUID, actor_user_id: uuid.UUID, now: datetime, source: str | None) -> None:
    rows = _lock_members(db, organization_id)
    actor = _actor(rows, actor_user_id)  # no administrator authority is needed to leave
    old_role = Role(actor.role)
    if old_role == Role.OWNER and _owners(rows) <= 1:
        raise LastOwner()
    db.delete(actor)
    db.flush()
    _event(db, "member_left", now, actor, f"{old_role}", source)
    _commit(db)


def repair_owner(db: Session, *, organization_id: uuid.UUID, user_id: uuid.UUID, now: datetime) -> None:
    """Operator only: make an EXISTING member the owner of an organization that has none (a legacy organization).

    Refuses an organization that already has an owner, a user who is not already a member (no membership is created
    here) and an unknown organization. It never runs over HTTP.
    """
    if db.get(Organization, organization_id) is None:
        raise MemberNotFound()
    rows = _lock_members(db, organization_id)
    if _owners(rows) > 0:
        raise RepairNotNeeded()
    row = next((r for r in rows if r.user_id == user_id), None)
    if row is None:
        raise NotAMember()
    previous = Role(row.role)
    ownership.allow_beyond_limit_in_this_transaction(db)  # the operator's repair is the one override of the limit
    row.role = Role.OWNER
    db.flush()
    db.add(SecurityEvent(occurred_at=now, event_type="owner_repaired", actor_user_id=user_id, organization_id=organization_id, detail=f"cli {previous}>owner"))
    _commit(db)


def transfer_ownership(
    db: Session, *, organization_id: uuid.UUID, actor_user_id: uuid.UUID, membership_id: uuid.UUID, now: datetime, source: str | None
) -> None:
    """An owner hands ownership to another member: the target becomes an owner and the actor an admin (so they can
    leave afterwards, or stay). Decided from fresh, locked rows like every membership change; the owner count can
    never drop, because the target becomes an owner in the same transaction."""
    rows = _lock_members(db, organization_id)
    actor = _actor(rows, actor_user_id)
    if Role(actor.role) != Role.OWNER:
        raise NotAllowed("not_owner", "Only an owner can transfer ownership.")
    target = _target(rows, membership_id)
    if target.id == actor.id:
        raise NotAllowed("self_transfer", "Choose another member to transfer ownership to.")
    if Role(target.role) == Role.OWNER:
        raise NotAllowed("already_owner", "That member is already an owner.")
    previous = Role(target.role)
    ownership.ensure_can_own_another(db, target.user_id)  # receiving ownership counts against the person's limit
    target.role = Role.OWNER
    actor.role = Role.ADMIN
    db.flush()
    _event(db, "ownership_transferred", now, actor, f"{target.user_id} {previous}>owner", source)
    _commit(db)


def lock_as_owner(db: Session, *, organization_id: uuid.UUID, actor_user_id: uuid.UUID) -> list[OrganizationUser]:
    """Lock the organization's memberships (the usual order) and require the actor to be an owner NOW."""
    rows = _lock_members(db, organization_id)
    if Role(_actor(rows, actor_user_id).role) != Role.OWNER:
        raise NotAllowed("not_owner", "Only an owner can do this.")
    return rows
