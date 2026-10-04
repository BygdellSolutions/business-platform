"""Operator repair of tenant data (shell and database access required; never over HTTP).

    python -m app.scripts.repair owner --organization-id <uuid> --email someone@example.com

Makes an EXISTING member the owner of an organization that has no owner (a legacy organization created before
every organization began with one). It is deliberately narrow and explicit:

  * both identifiers are required; nothing is chosen for you;
  * the user must already be a member: this command never creates a membership;
  * an organization that already has an owner is refused (use the application's membership administration);
  * it changes that one membership's role and records an `owner_repaired` security event (ids and roles only).

It lives apart from `app.scripts.admin` because that module is authentication-only (users, credentials, sessions)
and knows nothing about organizations; this one is tenant-aware by design.
"""

import argparse
import sys
import uuid

from sqlalchemy import select

from app.core import clock, memberships, security_events
from app.core.db import SessionLocal
from app.models import User


class RepairError(Exception):
    pass


def repair_owner(db, *, organization_id: uuid.UUID, email: str) -> None:
    user = db.scalar(select(User).where(User.email == security_events.normalize_email(email)))
    if user is None:
        raise RepairError("There is no user with that email.")
    try:
        memberships.repair_owner(db, organization_id=organization_id, user_id=user.id, now=clock.utcnow())
    except memberships.MemberNotFound:
        raise RepairError("There is no organization with that id.")
    except memberships.NotAMember:
        raise RepairError("That user is not a member of the organization. Nothing was changed (this command never creates a membership).")
    except memberships.RepairNotNeeded:
        raise RepairError("The organization already has an owner. Nothing was changed.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.scripts.repair", description="Operator repair of tenant data (shell access required).")
    commands = parser.add_subparsers(dest="command", required=True)
    owner = commands.add_parser("owner", help="make an existing member the owner of an organization that has none")
    owner.add_argument("--organization-id", required=True, type=uuid.UUID)
    owner.add_argument("--email", required=True)
    args = parser.parse_args(argv)
    try:
        with SessionLocal() as db:
            repair_owner(db, organization_id=args.organization_id, email=args.email)
        print("The member is now the owner of the organization.")
    except RepairError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
