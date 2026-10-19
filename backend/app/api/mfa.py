"""A person's own second factor (AUTH_MODE=session only): see its state, enrol, confirm, regenerate recovery codes, disable.

Called by the BFF for the signed-in person; every operation is about the CALLER alone. Starting to enrol needs the password again
(a stolen session must not be able to attach an attacker's authenticator); regenerating codes and disabling need a current code.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.api.auth import _busy, _throttled, session_mode_only
from app.core import auth_service, clock, mfa_policy, mfa_service, reauth, security_events
from app.core.auth import get_current_user
from app.core.db import get_db
from app.models import User
from app.schemas.auth import MfaCodeRequest, MfaEnrollment, MfaPasswordRequest, MfaStatus, RecoveryCodes

router = APIRouter(prefix="/api/auth/mfa", tags=["auth"], dependencies=[Depends(session_mode_only)])


def _invalid_code() -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "invalid_code", "message": "That code is not correct."})


def _already() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail={"code": "already_enabled", "message": "A second factor is already on."})


def _not_on() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail={"code": "not_enabled", "message": "No second factor is set up."})


@router.get("", response_model=MfaStatus)
def read_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> MfaStatus:
    enabled = mfa_service.is_enabled(db, user.id)
    return MfaStatus(enabled=enabled, required=mfa_policy.required_for_user(db, user), recovery_codes_left=mfa_service.recovery_codes_left(db, user.id) if enabled else 0)


@router.post("/enroll", response_model=MfaEnrollment)
def enroll(payload: MfaPasswordRequest, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> MfaEnrollment:
    """Start enrolling: proves the password, then returns the key to put into an authenticator app. Not in force until confirmed."""
    try:
        reauth.confirm_recent_authentication(db, user=user, password=payload.password, source=security_events.client_source(request))
    except reauth.RecentAuthenticationFailed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "wrong_password", "message": "The password is not correct."})
    except auth_service.Throttled as error:
        raise _throttled(error)
    except auth_service.AuthBusy:
        raise _busy()
    try:
        enrollment = mfa_service.begin_enrollment(db, user, clock.utcnow())
    except mfa_service.AlreadyEnabled:
        raise _already()
    return MfaEnrollment(secret=enrollment.secret_base32, uri=enrollment.uri)


@router.post("/confirm", response_model=RecoveryCodes)
def confirm(payload: MfaCodeRequest, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> RecoveryCodes:
    """The person typed a code from the app: the second factor is on. The recovery codes are shown ONCE, in this answer."""
    try:
        codes = mfa_service.confirm_enrollment(db, user, payload.code, clock.utcnow(), security_events.client_source(request))
    except mfa_service.InvalidCode:
        raise _invalid_code()
    except mfa_service.AlreadyEnabled:
        raise _already()
    except mfa_service.NotEnabled:
        raise _not_on()
    except auth_service.Throttled as error:
        raise _throttled(error)
    return RecoveryCodes(recovery_codes=codes)


@router.post("/recovery-codes", response_model=RecoveryCodes)
def regenerate(payload: MfaCodeRequest, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> RecoveryCodes:
    """New recovery codes (the old ones stop working); needs a current code."""
    try:
        codes = mfa_service.regenerate_recovery_codes(db, user, payload.code, clock.utcnow(), security_events.client_source(request))
    except mfa_service.InvalidCode:
        raise _invalid_code()
    except mfa_service.NotEnabled:
        raise _not_on()
    except auth_service.Throttled as error:
        raise _throttled(error)
    return RecoveryCodes(recovery_codes=codes)


@router.post("/disable", status_code=status.HTTP_204_NO_CONTENT)
def disable(payload: MfaCodeRequest, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    """Turn the second factor off; needs a current code. An owner or admin cannot while the policy requires one."""
    if mfa_policy.required_for_user(db, user):
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "required", "message": "Your role requires a second factor."})
    try:
        mfa_service.disable(db, user, payload.code, clock.utcnow(), security_events.client_source(request))
    except mfa_service.InvalidCode:
        raise _invalid_code()
    except mfa_service.NotEnabled:
        raise _not_on()
    except auth_service.Throttled as error:
        raise _throttled(error)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
