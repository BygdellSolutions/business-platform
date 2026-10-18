"""The support page's message box: a PUBLIC endpoint (a person who cannot sign in must be able to write), so it defends itself.

  * Whoever is signed in is recognised by the server (never by the form), so a ticket can say "a signed-in user".
  * A hidden field a person never sees (`website`) is filled only by programs: such a request is answered as if it worked and
    stores nothing.
  * Limits per source and per email address (hourly, counted from the tickets themselves, so they survive a restart), and a
    global valve that answers "busy" instead of letting the table fill.
  * The text is bounded and the address must look like an address; nothing the sender wrote is ever put into the confirmation
    email (see app.core.support).
  * The ticket is stored and committed BEFORE any email is tried; a failed email never fails the request.
"""

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator
from sqlalchemy.orm import Session

from app.core import clock, rate_limit, support
from app.core.auth import get_current_user
from app.core.config import settings
from app.core.db import get_db
from app.core.security_events import client_source
from app.models import User
from app.models.support_ticket import MAX_MESSAGE

router = APIRouter(prefix="/api/support", tags=["support"])

# Deliberately plain: one "@", no spaces, a dot in the domain. Whether the address works is for the confirmation email to show.
EMAIL = re.compile(r"^[^@\s<>\",;]+@[^@\s<>\",;]+\.[^@\s<>\",;]{2,}$")


class SupportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)] | None = None
    email: Annotated[str, StringConstraints(strip_whitespace=True, max_length=254)]
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=MAX_MESSAGE)]
    website: str | None = None  # the honeypot: a real person never sees this field

    @field_validator("email")
    @classmethod
    def looks_like_an_address(cls, value: str) -> str:
        if EMAIL.fullmatch(value) is None:
            raise ValueError("Enter an email address we can answer, like name@example.com.")
        return value


class SupportReceipt(BaseModel):
    reference: str


def optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    """The signed-in user, or None. A missing or stale session is simply "not signed in" here: the box is open to everyone."""
    try:
        return get_current_user(request, db)
    except HTTPException:
        return None


@router.post("", response_model=SupportReceipt, status_code=status.HTTP_201_CREATED)
def send_message(
    payload: SupportRequest, request: Request, user: User | None = Depends(optional_user), db: Session = Depends(get_db)
) -> SupportReceipt:
    if payload.website:  # a program filled the trap: look successful, keep nothing
        return SupportReceipt(reference=support.new_reference())
    source = client_source(request)
    now = clock.utcnow()
    since = support.window_start(now)
    limit = settings.support_tickets_per_hour
    rate_limit.enforce("support", f"s:{source}", limit, window=3600.0)
    if support.count_since(db, since, email_lower=payload.email.lower()) >= limit:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail={"code": "rate_limited", "message": "You have sent several messages already. Wait a while and try again."}, headers={"Retry-After": "3600"})
    if support.count_since(db, since) >= settings.support_tickets_global_per_hour:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": "busy", "message": "Support is receiving a lot of messages. Try again later."}, headers={"Retry-After": "600"})

    # A signed-in sender's own name and address win over what the form says, so a ticket from an account is really from it.
    name = user.name if user else (payload.name or None)
    email = user.email if user else payload.email
    ticket = support.create_ticket(db, name=name, email=email, message=payload.message, user=user, source=source)
    support.deliver(db, ticket)
    return SupportReceipt(reference=ticket.reference)
