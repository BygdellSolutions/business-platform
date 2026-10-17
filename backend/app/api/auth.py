"""Authentication endpoints (AUTH_MODE=session only; in any other mode they do not exist: 404).

Called by the BFF, never by a browser. They answer "who is this?" and never anything about organizations.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import auth_service, clock, ownership, security_events, sessions
from app.core.auth import get_current_user
from app.core.config import settings
from app.core.db import get_db
from app.models import User
from app.models.auth import AuthSession
from app.schemas.auth import AuthUser, ChangePasswordRequest, LoginRequest, MfaLoginRequest, MfaRequiredResponse, SessionInfo, SessionResponse, SessionsEnded, SetupRequest

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
        user=AuthUser(id=str(user.id), email=user.email, name=user.name, can_create_organizations=ownership.created_count(db, user.id) < user.max_owned_organizations),
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


@router.post("/login", response_model=SessionResponse | MfaRequiredResponse, dependencies=[Depends(session_mode_only)])
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> SessionResponse | MfaRequiredResponse:
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
    if isinstance(issued, auth_service.MfaRequired):
        return MfaRequiredResponse(challenge=issued.challenge, expires_at=issued.expires_at)
    return _issued(db, issued)


@router.post("/login/mfa", response_model=SessionResponse, dependencies=[Depends(session_mode_only)])
def login_with_second_factor(payload: MfaLoginRequest, request: Request, db: Session = Depends(get_db)) -> SessionResponse:
    """Second step of a sign-in for a person with a second factor: the challenge from the first step and a code (or a recovery code).
    Wrong in any way is the same 401; too many wrong codes is a 429."""
    try:
        issued = auth_service.complete_login(
            db,
            challenge=payload.challenge,
            code=payload.code,
            source=security_events.client_source(request),
            user_agent=request.headers.get("user-agent"),
            presented_token=sessions.bearer_token(request),
        )
    except auth_service.LoginFailed:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired code")
    except auth_service.Throttled as error:
        raise _throttled(error)
    return _issued(db, issued)


@router.post("/setup", response_model=SessionResponse, dependencies=[Depends(session_mode_only)])
def redeem_setup_link(payload: SetupRequest, request: Request, db: Session = Depends(get_db)) -> SessionResponse:
    """Use a single-use set-password link (issued by the operator CLI): sets the password and signs in."""
    try:
        issued = auth_service.redeem_setup(
            db,
            token=payload.token,
            password=payload.password,
            name=payload.name,
            source=security_events.client_source(request),
            user_agent=request.headers.get("user-agent"),
        )
    except auth_service.PasswordProblem as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"code": "password_policy", "message": error.message})
    except auth_service.NameRequired:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"code": "name_required", "message": "Enter your name."})
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


# --- the caller's own sessions ---------------------------------------------------------------------------------------------------------------------
# A person sees and ends only THEIR sessions: every query is scoped to the authenticated user, and another user's session id
# answers 404 exactly like an unknown one. Ending is a mutating request, so get_current_user also demands the CSRF token.


@router.get("/sessions", response_model=list[SessionInfo], dependencies=[Depends(session_mode_only)])
def list_sessions(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[SessionInfo]:
    current = sessions.current_session(request)
    rows = db.scalars(
        select(AuthSession).where(AuthSession.user_id == user.id, *sessions.usable(clock.utcnow())).order_by(AuthSession.last_used_at.desc(), AuthSession.id)
    )
    return [SessionInfo(id=str(row.id), created_at=row.created_at, last_used_at=row.last_used_at, user_agent=row.user_agent, current=row.id == current.id) for row in rows]


@router.post("/sessions/end-others", response_model=SessionsEnded, dependencies=[Depends(session_mode_only)])
def end_other_sessions(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> SessionsEnded:
    """Sign out everywhere else: every session of the caller except this one."""
    now = clock.utcnow()
    ended = sessions.revoke_all_for_user(db, user.id, "logout", now, except_session_id=sessions.current_session(request).id)
    security_events.record(db, "logout", now, actor_user_id=user.id, source=security_events.client_source(request), detail=f"others={ended}")
    db.commit()
    return SessionsEnded(ended=ended)


@router.post("/sessions/end-all", response_model=SessionsEnded, dependencies=[Depends(session_mode_only)])
def end_all_sessions(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> SessionsEnded:
    """Sign out everywhere, this browser included."""
    now = clock.utcnow()
    ended = sessions.revoke_all_for_user(db, user.id, "logout", now)
    security_events.record(db, "logout", now, actor_user_id=user.id, source=security_events.client_source(request), detail=f"all={ended}")
    db.commit()
    return SessionsEnded(ended=ended)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(session_mode_only)])
def end_session(session_id: uuid.UUID, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    """End one of the caller's sessions (another person's, an ended one and an unknown one are all the same 404)."""
    now = clock.utcnow()
    row = db.scalar(select(AuthSession).where(AuthSession.id == session_id, AuthSession.user_id == user.id, *sessions.usable(now)))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    sessions.revoke(db, row.id, "logout", now)
    security_events.record(db, "logout", now, actor_user_id=user.id, source=security_events.client_source(request), detail="one")
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

