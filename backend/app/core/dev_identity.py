"""Development-only identity: who is calling, taken from a header or an env default.

This is the ONLY module that knows about the dev identity mechanism. It is used
solely by `app.core.auth.get_current_user` when AUTH_MODE=dev. Replacing it with
production authentication means changing that one branch, not any endpoint.

It identifies the *user* only. It never says anything about which organization
the user may act in; that is decided by `app.core.tenant` from memberships.
"""

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import User

DEV_USER_HEADER = "X-Dev-User-Email"


def resolve_dev_user(request: Request, db: Session) -> User | None:
    email = request.headers.get(DEV_USER_HEADER) or settings.dev_user_email
    if not email:
        return None
    return db.scalar(select(User).where(User.email == email.strip().lower()))
