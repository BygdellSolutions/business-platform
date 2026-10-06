import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core import db, readiness
from app.core.logging_config import log

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process answers. Depends on nothing (no database), so a database outage never makes an orchestrator
    think the process is dead. `async` on purpose: it runs on the event loop and never waits for a worker thread, so even a
    pool of threads stuck on a hung database cannot make a live process look dead."""
    return {"status": "ok"}


@router.get("/health/ready")
def health_ready() -> JSONResponse:
    """Readiness: the database is reachable and its Alembic revision is exactly this image's head (see
    `app.core.readiness`). Never migrates. The answer is coarse on purpose; the reason goes to the log only."""
    reason = readiness.check_bounded(db.engine)
    if reason == readiness.READY:
        return JSONResponse({"status": "ready"}, headers={"cache-control": "no-store"})
    log(logging.WARNING, "not_ready", reason=reason)
    return JSONResponse({"status": "unready"}, status_code=503, headers={"cache-control": "no-store"})
