"""BFF -> FastAPI internal authentication: a shared secret the BFF sends on EVERY request.

Defence in depth ONLY. The network still has to keep everything but the BFF away from FastAPI; this makes a request
that somehow arrives from elsewhere (a misrouted proxy, another container on the network) useless without the secret.

  * enforced whenever BFF_INTERNAL_SECRET is set (always in production, see `Settings`); a development run without it
    is the one documented unauthenticated mode;
  * checked BEFORE anything of the application runs (routing, sessions, bodies), with a constant-time comparison;
  * `/health` and `/health/ready` are exempt: they are coarse and private, so an orchestrator can probe them without
    holding the secret;
  * the secret is never logged, never returned and never part of a response; a refusal carries the same fixed body
    whatever was wrong, plus a response header the BFF reads to tell it from an application 403.
"""

import hmac

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings

EXEMPT_PATHS = frozenset({"/health", "/health/ready"})
REFUSED_HEADER = "x-internal-auth"
AUTHENTICATED_KEY = "bp_internal_authenticated"


def is_authenticated(scope: Scope) -> bool:
    """True when this request carried the correct secret (always False when no secret is configured)."""
    return scope.get(AUTHENTICATED_KEY) is True


class InternalAuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        secret = settings.bff_internal_secret
        if scope["type"] not in ("http", "websocket") or secret is None:
            await self.app(scope, receive, send)
            return
        expected = secret.get_secret_value().encode("utf-8")
        header = settings.bff_internal_header.lower().encode("ascii")
        provided = next((value for name, value in scope.get("headers", []) if name == header), b"")
        if hmac.compare_digest(provided, expected):
            scope[AUTHENTICATED_KEY] = True
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http" and scope.get("path") in EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        response = JSONResponse(
            {"detail": {"code": "internal_auth_failed", "message": "Forbidden"}},
            status_code=403,
            headers={REFUSED_HEADER: "rejected", "cache-control": "no-store"},
        )
        await response(scope, receive, send)
