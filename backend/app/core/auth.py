from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core import dev_identity, sessions
from app.core.config import settings
from app.core.db import get_db
from app.models import User


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Authenticate the caller: the single seam where "who is this user?" is answered.

    Exactly one mechanism is active, chosen by AUTH_MODE, and there is never a fallback from one to another:

      session   an opaque server-side session (`Authorization: Bearer`), the only mode allowed in production
      dev       the development identity header (APP_ENV=development only)
      disabled  nobody is authenticated

    In session mode the development header and DEV_USER_EMAIL are not even read.
    """
    user: User | None = None
    if settings.auth_mode == "session":
        user = sessions.authenticate_request(request, db)
    elif settings.auth_mode == "dev":
        user = dev_identity.resolve_dev_user(request, db)

    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user
