import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app import registrations
from app.api import auth, customers, health, history, invitations, items, me, members, organization, organizations, suppliers
from app.core.admission import AdmissionMiddleware
from app.core.config import settings
from app.core.entity_registry import registry
from app.core.internal_auth import InternalAuthMiddleware
from app.core.logging_config import configure_logging, log
from app.core.request_context import RequestContextMiddleware
from app.modules import custom_fields, equine, inventory, invoicing, sales

configure_logging("backend")

app = FastAPI(title="business-platform")

def configure_middleware(application: FastAPI, cors_origins: list[str]) -> None:
    """Middleware order: the LAST one added is the outermost. A request meets, in turn: the request context (id, the one
    safe log line, response headers; it also sees a refused request), CORS (development only), the BFF internal-secret
    check, admission (the innermost, so a request that is refused anyway never takes a slot), and only then routing and
    the application."""
    application.add_middleware(AdmissionMiddleware)
    application.add_middleware(InternalAuthMiddleware)
    if cors_origins:
        # Production configures none (the browser never calls FastAPI), and then the middleware is not installed at all.
        application.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    application.add_middleware(RequestContextMiddleware)


configure_middleware(app, settings.cors_origins or [])


@app.exception_handler(OperationalError)
@app.exception_handler(InterfaceError)
async def database_unavailable(request: Request, error: Exception) -> JSONResponse:
    """A database that cannot be reached is a fixed, coarse 503 (never the driver's message, which can name the host)."""
    # The SQLSTATE says WHY (57014 a statement ran too long, 55P03 a lock wait ran out, 57P01/08xxx the connection is gone)
    # without any of the driver's message text.
    sqlstate = getattr(getattr(error, "orig", None), "sqlstate", None)
    log(
        logging.ERROR,
        "database_unavailable",
        error_type=type(error).__name__,
        sqlstate=sqlstate if isinstance(sqlstate, str) else "",
        request_id=request.scope.get("bp_request_id", ""),
    )
    return JSONResponse(
        {"detail": {"code": "service_unavailable", "message": "The service is temporarily unavailable."}},
        status_code=503,
        headers={"Retry-After": "5", "cache-control": "no-store"},
    )


@app.exception_handler(PoolTimeoutError)
async def pool_exhausted(request: Request, error: Exception) -> JSONResponse:
    """No database connection became free within the pool timeout: the process is saturated, so say so at once (a fixed 503
    and a short Retry-After, the same answer admission gives) instead of letting the request hang or fail as a 500."""
    log(logging.ERROR, "pool_exhausted", request_id=request.scope.get("bp_request_id", ""))
    return JSONResponse(
        {"detail": {"code": "overloaded", "message": "The service is busy. Try again in a moment."}},
        status_code=503,
        headers={"Retry-After": "1", "cache-control": "no-store"},
    )


app.include_router(health.router)
app.include_router(auth.router)
app.include_router(me.router)
app.include_router(customers.router)
app.include_router(suppliers.router)
app.include_router(items.router)
app.include_router(history.router)
app.include_router(organization.router)
app.include_router(organizations.router)
app.include_router(members.router)
app.include_router(invitations.router)
app.include_router(invitations.public_router)
app.include_router(equine.router)
app.include_router(sales.router)
app.include_router(custom_fields.router)
app.include_router(invoicing.router)
app.include_router(invoicing.invoiceable_router)
app.include_router(inventory.router)
app.include_router(inventory.availability_router)

# Modules register what they expose to generic capabilities; nothing imports a module
# except here. validate() fails fast at startup on an inconsistent registration.
registrations.register(registry)
equine.register(registry)
sales.register(registry)
custom_fields.register(registry)
invoicing.register(registry)
inventory.register(registry)
registry.validate()
