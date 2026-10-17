from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core import metrics

router = APIRouter(tags=["internal"])


@router.get("/internal/metrics")
async def internal_metrics() -> JSONResponse:
    """The operator's view of this process: request counts by status class and time bucket, refusals, admission and pool
    levels. Not under /api (the BFF never forwards it to a browser) and not exempt from the internal-secret check, so only
    something that holds the BFF secret can read it. `async`: it never waits for a worker thread or the database, so it still
    answers while the process is saturated."""
    return JSONResponse(metrics.snapshot(), headers={"cache-control": "no-store"})
