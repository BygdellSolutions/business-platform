"""Authentication endpoints (AUTH_MODE=session only; in any other mode they do not exist: 404).

Called by the BFF, never by a browser. They answer "who is this?" and never anything about organizations.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core import auth_service, clock, ownership, security_events, sessions
from app.core.auth import get_current_user
from app.core.config import settings
from app.core.db import get_db
from app.models import User
from app.schemas.auth import AuthUser, ChangePasswordRequest, LoginRequest, SessionResponse, SetupRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])


def session_mode_only() -> None:
    if settings.auth_mode != "session":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")


def _issued(db: Session, issued: auth_service.SessionIssued) -> SessionResponse:
    user = issued.user
    return SessionResponse(
        token=issued.token,
        csrf_token=issued.csrf_token,
        expires_at=issued.expires_at,
        user=AuthUser(id=str(user.id), email=user.email, name=user.name, can_create_organizations=ownership.owned_count(db, user.id) < user.max_owned_organizations),
    )


def _throttled(error: auth_service.Throttled) -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        detail={"code": "throttled", "message": "Too many attempts. Try again later."},
        headers={"Retry-After": str(error.retry_after)},
    )


def _busy() -> HTTPException:
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "auth_busy", "message": "The service is busy. Try again in a moment."},
        headers={"Retry-After": "1"},
    )


@router.post("/login", response_model=SessionResponse, dependencies=[Depends(session_mode_only)])
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> SessionResponse:
    """Password login. Wrong credentials of every kind look identical (401); see `app.core.auth_service`."""
    try:
        issued = auth_service.login(
            db,
            email=payload.email,
            password=payload.password,
            source=security_events.client_source(request),
            user_agent=request.headers.get("user-agent"),
            presented_token=sessions.bearer_token(request),
        )
    except auth_service.LoginFailed:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    except auth_service.Throttled as error:
        raise _throttled(error)
    except auth_service.AuthBusy:
        raise _busy()
    return _issued(db, issued)


@router.post("/setup", response_model=SessionResponse, dependencies=[Depends(session_mode_only)])
def redeem_setup_link(payload: SetupRequest, request: Request, db: Session = Depends(get_db)) -> SessionResponse:
    """Use a single-use set-password link (issued by the operator CLI): sets the password and signs in."""
    try:
        issued = auth_service.redeem_setup(
            db,
            token=payload.token,
            password=payload.password,
            source=security_events.client_source(request),
            user_agent=request.headers.get("user-agent"),
        )
    except auth_service.PasswordProblem as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"code": "password_policy", "message": error.message})
    except auth_service.InvalidSetupLink:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "invalid_setup_link", "message": "This link is invalid or has expired."})
    except auth_service.Throttled as error:
        raise _throttled(error)
    except auth_service.AuthBusy:
        raise _busy()
    return _issued(db, issued)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(session_mode_only)])
def logout(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    session = sessions.current_session(request)
    now = clock.utcnow()
    sessions.revoke(db, session.id, "logout", now)
    security_events.record(db, "logout", now, actor_user_id=user.id, source=security_events.client_source(request))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(session_mode_only)])
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Change the caller's own password; every OTHER session of the user ends."""
    try:
        auth_service.change_password(
            db,
            user=user,
            session=sessions.current_session(request),
            current_password=payload.current_password,
            new_password=payload.new_password,
            source=security_events.client_source(request),
        )
    except auth_service.PasswordProblem as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"code": "password_policy", "message": error.message})
    except auth_service.WrongCurrentPassword:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "wrong_current_password", "message": "The current password is not correct."})
    except auth_service.Throttled as error:
        raise _throttled(error)
    except auth_service.AuthBusy:
        raise _busy()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
