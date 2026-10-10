"""Operator commands: the only way to create the first user, recover one, or disable one.

    python -m app.scripts.admin bootstrap-user --email owner@example.com --name "Ada Owner"
    python -m app.scripts.admin reissue-setup-link --email owner@example.com
    python -m app.scripts.admin disable-user --email someone@example.com
    python -m app.scripts.admin enable-user --email someone@example.com
    python -m app.scripts.admin grant-org-creation --email someone@example.com
    python -m app.scripts.admin revoke-org-creation --email someone@example.com
    python -m app.scripts.admin purge

There is no way to give anyone a password here, on purpose: a command line argument or an environment
variable would leave a password in a shell history or a configuration. Instead the command prints a SINGLE-USE
link (`<PUBLIC_ORIGIN>/setup#<token>`) that expires (SETUP_TOKEN_TTL_HOURS) and lets the person choose their
own password. The token is shown once, here, and stored only as a hash. Running these commands needs shell and
database access, which is the same trust as the database itself; there is no standing endpoint that can create
a user without an invitation or a setup link, so this is not a backdoor that outlives its use.
"""

import argparse
import re
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import auth_service, clock, ownership, security_events, sessions
from app.core.config import settings
from app.core.db import SessionLocal
from app.models import User

EMAIL = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,255}\.[^\s@]{1,63}$")


class OperatorError(Exception):
    pass


def _user_by_email(db: Session, email: str) -> User:
    user = db.scalar(select(User).where(User.email == security_events.normalize_email(email)))
    if user is None:
        raise OperatorError("There is no user with that email.")
    return user


def setup_link(token: str) -> str:
    return f"{settings.public_origin.rstrip('/')}/setup#{token}"


def bootstrap_user(db: Session, *, email: str, name: str, can_create_organizations: bool = True) -> str:
    # can_create_organizations=False gives the account an owned-organization limit of 0 (it may own none).
    """Create a user WITHOUT a credential and return a single-use set-password link."""
    normalized = security_events.normalize_email(email)
    if not EMAIL.fullmatch(normalized) or len(normalized) > 320:
        raise OperatorError("That does not look like an email address.")
    if not name.strip():
        raise OperatorError("A name is required.")
    if db.scalar(select(User.id).where(User.email == normalized)) is not None:
        raise OperatorError("A user with that email already exists. Use reissue-setup-link to recover access.")
    user = User(email=normalized, name=name.strip(), max_owned_organizations=1 if can_create_organizations else 0)
    db.add(user)
    db.flush()
    token = auth_service.issue_setup_token(db, user, clock.utcnow(), detail="bootstrap")
    db.commit()
    return setup_link(token)


def reissue_setup_link(db: Session, *, email: str) -> str:
    """A new single-use link for an existing user (recovery). Earlier outstanding links stop working."""
    user = _user_by_email(db, email)
    if not user.is_active:
        raise OperatorError("That user is disabled. Enable the user first.")
    token = auth_service.issue_setup_token(db, user, clock.utcnow(), detail="reissue")
    db.commit()
    return setup_link(token)


def disable_user(db: Session, *, email: str) -> int:
    revoked = auth_service.set_user_active(db, _user_by_email(db, email), False, clock.utcnow())
    db.commit()
    return revoked


def enable_user(db: Session, *, email: str) -> None:
    auth_service.set_user_active(db, _user_by_email(db, email), True, clock.utcnow())
    db.commit()


def set_org_creation(db: Session, *, email: str, allowed: bool) -> bool:
    """Compatibility with the former yes/no right: grant = room for one more owned organization than the account owns
    now (never lowering a larger limit); revoke = exactly what it owns now (it keeps them, gains no more)."""
    user = _user_by_email(db, email)
    owned = ownership.owned_count(db, user.id)
    limit = max(user.max_owned_organizations, owned + 1) if allowed else owned
    changed = auth_service.set_owned_limit(db, user, limit, clock.utcnow())
    db.commit()
    return changed


def set_owned_limit(db: Session, *, email: str, limit: int) -> bool:
    """How many organizations the account may own. Memberships and roles are never touched."""
    if limit < 0:
        raise OperatorError("The limit cannot be negative.")
    changed = auth_service.set_owned_limit(db, _user_by_email(db, email), limit, clock.utcnow())
    db.commit()
    return changed


def purge(db: Session) -> dict[str, int]:
    now = clock.utcnow()
    counts = sessions.purge_records(db, now)
    counts["security_events"] = security_events.purge_events(db, now)
    db.commit()
    return counts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.scripts.admin", description="Operator commands (shell access required).")
    commands = parser.add_subparsers(dest="command", required=True)

    bootstrap = commands.add_parser("bootstrap-user", help="create a user without a password and print a single-use setup link")
    bootstrap.add_argument("--email", required=True)
    bootstrap.add_argument("--name", required=True)
    bootstrap.add_argument("--no-org-creation", action="store_true", help="do not allow this user to create organizations")

    for name, help_text in (
        ("reissue-setup-link", "print a new single-use setup link for an existing user (recovery)"),
        ("disable-user", "disable a user and end all of their sessions"),
        ("enable-user", "enable a user again"),
        ("grant-org-creation", "let a user own one more organization than now (raises the owned-organization limit if needed)"),
        ("revoke-org-creation", "stop a user from owning more organizations (the limit becomes what they own now)"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--email", required=True)

    limit = commands.add_parser("set-owned-limit", help="set how many organizations a user may own (an account entitlement, not a role)")
    limit.add_argument("--email", required=True)
    limit.add_argument("--limit", required=True, type=int)

    commands.add_parser("purge", help="delete expired sessions, used or expired setup tokens and old security events")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with SessionLocal() as db:
            if args.command == "bootstrap-user":
                link = bootstrap_user(db, email=args.email, name=args.name, can_create_organizations=not args.no_org_creation)
                print(f"User created without a password. Give this single-use link to the person (valid {settings.setup_token_ttl_hours} hours):\n\n  {link}\n")
            elif args.command == "reissue-setup-link":
                print(f"Single-use link (valid {settings.setup_token_ttl_hours} hours; earlier links no longer work):\n\n  {reissue_setup_link(db, email=args.email)}\n")
            elif args.command == "disable-user":
                print(f"User disabled; {disable_user(db, email=args.email)} session(s) ended.")
            elif args.command == "enable-user":
                enable_user(db, email=args.email)
                print("User enabled. They have no sessions; they sign in again.")
            elif args.command in ("grant-org-creation", "revoke-org-creation"):
                allowed = args.command == "grant-org-creation"
                changed = set_org_creation(db, email=args.email, allowed=allowed)
                print(("Organization creation allowed." if allowed else "Organization creation no longer allowed.") + ("" if changed else " (no change)"))
            elif args.command == "set-owned-limit":
                changed = set_owned_limit(db, email=args.email, limit=args.limit)
                print(f"Owned-organization limit set to {args.limit}." + ("" if changed else " (no change)"))
            elif args.command == "purge":
                print(", ".join(f"{name}: {count}" for name, count in purge(db).items()))
    except OperatorError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
