"""Operator commands: the only way to create the first user, recover one, or disable one.

    python -m app.scripts.admin bootstrap-user --email owner@example.com
    python -m app.scripts.admin reissue-setup-link --email owner@example.com
    python -m app.scripts.admin disable-user --email someone@example.com
    python -m app.scripts.admin enable-user --email someone@example.com
    python -m app.scripts.admin grant-org-creation --email someone@example.com
    python -m app.scripts.admin revoke-org-creation --email someone@example.com
    python -m app.scripts.admin revoke-all-sessions --yes-end-all-sessions   # emergency, after a restore or an incident
    python -m app.scripts.admin erase-account --email someone@example.com --yes-erase   # right to erasure (an account in no organization)
    python -m app.scripts.admin reset-mfa --email someone@example.com   # lost the authenticator and the recovery codes
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


def setup_link(token: str, *, ask_name: bool = False) -> str:
    # `?new` only tells the page to ask for the person's name (not a secret; the backend still decides).
    return f"{settings.public_origin.rstrip('/')}/setup{'?new' if ask_name else ''}#{token}"


def bootstrap_user(db: Session, *, email: str, name: str = "", can_create_organizations: bool = True) -> str:
    # Without a name the person enters their own on the setup page (an empty name until then).
    # can_create_organizations=False gives the account an owned-organization limit of 0 (it may own none).
    """Create a user WITHOUT a credential and return a single-use set-password link."""
    normalized = security_events.normalize_email(email)
    if not EMAIL.fullmatch(normalized) or len(normalized) > 320:
        raise OperatorError("That does not look like an email address.")
    if db.scalar(select(User.id).where(User.email == normalized)) is not None:
        raise OperatorError("A user with that email already exists. Use reissue-setup-link to recover access.")
    user = User(email=normalized, name=name.strip(), max_owned_organizations=1 if can_create_organizations else 0)
    db.add(user)
    db.flush()
    token = auth_service.issue_setup_token(db, user, clock.utcnow(), detail="bootstrap")
    db.commit()
    return setup_link(token, ask_name=not user.name)


def reissue_setup_link(db: Session, *, email: str) -> str:
    """A new single-use link for an existing user (recovery). Earlier outstanding links stop working."""
    user = _user_by_email(db, email)
    if not user.is_active:
        raise OperatorError("That user is disabled. Enable the user first.")
    token = auth_service.issue_setup_token(db, user, clock.utcnow(), detail="reissue")
    db.commit()
    return setup_link(token, ask_name=not user.name)


def disable_user(db: Session, *, email: str) -> int:
    revoked = auth_service.set_user_active(db, _user_by_email(db, email), False, clock.utcnow())
    db.commit()
    return revoked


def enable_user(db: Session, *, email: str) -> None:
    auth_service.set_user_active(db, _user_by_email(db, email), True, clock.utcnow())
    db.commit()


def set_org_creation(db: Session, *, email: str, allowed: bool) -> bool:
    """Compatibility with the former yes/no right: grant = room to create one more organization than the account has
    created (never lowering a larger limit); revoke = exactly what it has created (it keeps them, creates no more)."""
    user = _user_by_email(db, email)
    created = ownership.created_count(db, user.id)
    limit = max(user.max_owned_organizations, created + 1) if allowed else created
    changed = auth_service.set_owned_limit(db, user, limit, clock.utcnow())
    db.commit()
    return changed


def set_owned_limit(db: Session, *, email: str, limit: int) -> bool:
    """How many organizations the account may create. Memberships and roles are never touched."""
    if limit < 0:
        raise OperatorError("The limit cannot be negative.")
    changed = auth_service.set_owned_limit(db, _user_by_email(db, email), limit, clock.utcnow())
    db.commit()
    return changed


def revoke_all_sessions(db: Session) -> int:
    """Emergency: end every active session of every user, in one transaction, and record one security event (the count, no
    names, no token). Used after a restore from a backup or a suspected leak, so no restored or stolen session survives.
    People sign in again with their passwords; nothing else changes (no user is disabled, no setup link is issued)."""
    now = clock.utcnow()
    ended = sessions.revoke_all(db, "operator", now)
    security_events.record(db, "sessions_revoked_by_operator", now, detail=f"count={ended}")
    db.commit()
    return ended


def erase_account(db: Session, *, email: str) -> None:
    """Right to erasure for an ACCOUNT: anonymise it (no email, no name, no credential, no session, no second factor). Refused while the
    account still belongs to an organization; records an organization keeps are not touched (see app.core.account_data)."""
    from app.core import account_data

    try:
        account_data.erase_account(db, _user_by_email(db, email), clock.utcnow())
    except account_data.ErasureRefused as error:
        raise OperatorError(str(error)) from error


def reset_mfa(db: Session, *, email: str) -> None:
    """For a person who lost both their authenticator and their recovery codes: remove the second factor and end every session of
    theirs. They sign in with their password and set it up again (an owner or admin cannot work until they do)."""
    from app.core import mfa_service

    mfa_service.reset_by_operator(db, _user_by_email(db, email), clock.utcnow())


def failed_jobs_report(db: Session) -> list[str]:
    """One line per job that failed for good: id, queue, task, attempts (arguments are ids only; they are shown for diagnosis)."""
    from app.core import jobs

    return [f"{job['id']}  {job['queue_name']}  {job['task_name']}  attempts={job['attempts']}  args={job['args']}" for job in jobs.failed_jobs(db)]


def retry_job(db: Session, *, job_id: int) -> None:
    from app.core import jobs

    if not jobs.retry_failed(db, job_id):
        raise OperatorError(f"There is no failed job {job_id}.")
    db.commit()


def support_tickets_report(db: Session) -> list[str]:
    """One line per OPEN ticket, newest first: reference, when, what happened to its email, who, and the first words. The full text is in `support-show`."""
    from app.core import support

    return [
        f"{t.reference}  {t.created_at:%Y-%m-%d %H:%M}  email={t.email_status}{'/' + t.email_error if t.email_error else ''}  {t.email}  {t.message[:60]!r}"
        for t in support.open_tickets(db)
    ]


def _ticket(db: Session, reference: str):
    from app.core import support

    ticket = support.ticket_by_reference(db, reference)
    if ticket is None:
        raise OperatorError(f"There is no ticket {reference}.")
    return ticket


def support_show(db: Session, *, reference: str) -> str:
    t = _ticket(db, reference)
    lines = [
        f"{t.reference}  {t.status}  {t.created_at.isoformat()}",
        f"From: {t.name or '-'} <{t.email}>  signed in: {'yes' if t.user_id else 'no'}",
        f"Email to support: {t.email_status} {t.email_error or ''}  confirmation: {t.confirmation_status or '-'}",
        "",
        t.message,
    ]
    return chr(10).join(lines)


def support_close(db: Session, *, reference: str) -> None:
    ticket = _ticket(db, reference)
    ticket.status = "closed"
    db.commit()


def support_resend(db: Session, *, reference: str) -> str:
    """Try the emails of a ticket again (after email was configured or a failure was fixed). Returns what happened."""
    from app.core import support

    ticket = _ticket(db, reference)
    support.deliver(db, ticket)
    db.refresh(ticket)
    return f"email to support: {ticket.email_status}{'/' + ticket.email_error if ticket.email_error else ''}"


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
    bootstrap.add_argument("--name", default="", help="optional: otherwise the person enters it on the setup page")
    bootstrap.add_argument("--no-org-creation", action="store_true", help="do not allow this user to create organizations")

    for name, help_text in (
        ("reissue-setup-link", "print a new single-use setup link for an existing user (recovery)"),
        ("disable-user", "disable a user and end all of their sessions"),
        ("enable-user", "enable a user again"),
        ("grant-org-creation", "let a user create one more organization than they have created (raises the limit if needed)"),
        ("revoke-org-creation", "stop a user from creating more organizations (the limit becomes what they have created)"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--email", required=True)

    limit = commands.add_parser("set-owned-limit", help="set how many organizations a user may create (an account entitlement, not a role)")
    limit.add_argument("--email", required=True)
    limit.add_argument("--limit", required=True, type=int)

    erase = commands.add_parser("erase-account", help="ERASE an account: anonymise it and remove every credential and session (it must belong to no organization)")
    erase.add_argument("--email", required=True)
    erase.add_argument("--yes-erase", action="store_true", dest="confirmed", help="required: confirms this cannot be undone")

    reset = commands.add_parser("reset-mfa", help="remove a person's second factor and end their sessions (they lost the app and the recovery codes)")
    reset.add_argument("--email", required=True)

    revoke_all = commands.add_parser("revoke-all-sessions", help="EMERGENCY: end every active session of every user (people sign in again)")
    revoke_all.add_argument("--yes-end-all-sessions", action="store_true", dest="confirmed", help="required: confirms this ends every session")

    commands.add_parser("failed-jobs", help="list the background jobs that failed for good")
    retry = commands.add_parser("retry-job", help="put one failed background job back in the queue (after the cause is fixed)")
    retry.add_argument("--id", required=True, type=int, dest="job_id")

    commands.add_parser("support-tickets", help="list the open support tickets (messages from the support page), newest first")
    for name, helptext in (("support-show", "print one support ticket in full"), ("support-close", "mark a support ticket as answered"), ("support-resend", "try the emails of a support ticket again (after email was set up)")):
        sub = commands.add_parser(name, help=helptext)
        sub.add_argument("--ref", required=True, dest="reference", help="the ticket number, like SUP-K7M2Q9XD")

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
            elif args.command == "erase-account":
                if not args.confirmed:
                    raise OperatorError("This anonymises the account for good. Run it again with --yes-erase to confirm.")
                erase_account(db, email=args.email)
                print("The account was erased: no email, name, credential, session or second factor remains.")
            elif args.command == "reset-mfa":
                reset_mfa(db, email=args.email)
                print("Second factor removed and every session ended. The person signs in with their password and sets it up again.")
            elif args.command == "revoke-all-sessions":
                if not args.confirmed:
                    raise OperatorError("This ends EVERY session of EVERY user. Run it again with --yes-end-all-sessions to confirm.")
                print(f"{revoke_all_sessions(db)} session(s) ended. Everyone signs in again.")
            elif args.command == "failed-jobs":
                lines = failed_jobs_report(db)
                print("\n".join(lines) if lines else "No failed jobs.")
            elif args.command == "retry-job":
                retry_job(db, job_id=args.job_id)
                print(f"Job {args.job_id} is queued again.")
            elif args.command == "support-tickets":
                lines = support_tickets_report(db)
                print(chr(10).join(lines) if lines else "No open tickets.")
            elif args.command == "support-show":
                print(support_show(db, reference=args.reference))
            elif args.command == "support-close":
                support_close(db, reference=args.reference)
                print("Ticket closed.")
            elif args.command == "support-resend":
                print(support_resend(db, reference=args.reference))
            elif args.command == "purge":
                print(", ".join(f"{name}: {count}" for name, count in purge(db).items()))
    except OperatorError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
