"""Recent authentication: proof that the person at the keyboard is the account holder, right now.

Destructive actions (leaving an organization, transferring its ownership, deleting it) require it. The concept is
deliberately not "the password": today the proof IS the password, checked like a password change (throttled per
user, failures recorded, bounded hashing work), and a future SSO or passkey account would prove itself its own way
through this same function.

The development identity has no passwords and authenticates nobody for real (development only, never production),
so it is exempt, exactly as it is exempt from signing in.
"""

from sqlalchemy.orm import Session

from app.core import clock, passwords, security_events, throttle
from app.core.config import settings
from app.core.passwords import admission
from app.models import User
from app.models.auth import UserCredential

MAX_PASSWORD_LENGTH = 1024


class RecentAuthenticationFailed(Exception):
    """The proof was missing or wrong."""


def confirm_recent_authentication(db: Session, *, user: User, password: str | None, source: str | None) -> None:
    """Return quietly if the caller proved who they are; raise RecentAuthenticationFailed otherwise (and
    throttle.Throttled after too many failures, AuthBusy when hashing capacity is exhausted). A failure is
    recorded and committed, so it counts even though the action itself is refused."""
    if settings.auth_mode != "session":
        return
    now = clock.utcnow()
    throttle.check_actor_failures(db, now, "reauth_failure", settings.password_change_max_failures, user.id)
    correct = False
    if password and len(password) <= MAX_PASSWORD_LENGTH:
        with admission.slot():
            credential = db.get(UserCredential, user.id)
            stored = credential.password_hash if credential is not None else None
            correct = stored is not None and passwords.verify(stored, password)
    if not correct:
        security_events.record(db, "reauth_failure", now, actor_user_id=user.id, source=source)
        db.commit()
        raise RecentAuthenticationFailed()
