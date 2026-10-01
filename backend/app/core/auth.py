from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core import dev_identity
from app.core.config import settings
from app.core.db import get_db
from app.models import User


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Authenticate the caller. The single seam where real authentication plugs in."""
    user: User | None = None
    if settings.auth_mode == "dev":
        user = dev_identity.resolve_dev_user(request, db)
    # Production authentication (sessions/JWT) will resolve `user` here instead.

    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user
