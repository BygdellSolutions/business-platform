"""Tasks that exist only for the job-queue tests; loaded into the worker process through WORKER_TASK_MODULES."""

import os

from app.core.db import SessionLocal
from app.core.jobs import once
from app.worker.queue import app


@app.task(name="probe.fail_once", queue="maintenance")
def fail_once(key: str) -> None:
    """Fails the first time (no retry configured: it fails for good) and succeeds when it is run again after a manual retry."""
    with SessionLocal() as db:
        first = once(db, f"fail-once:{key}")
        db.commit()
    if first:
        raise RuntimeError("the first attempt fails")


@app.task(name="probe.apply", queue="maintenance")
def apply(key: str, die_after_effect: bool = False) -> None:
    """An effect that must happen once (a stand-in for money or stock). Dies, if asked, AFTER the effect is committed and
    BEFORE the queue is told: the classic duplicate-delivery window."""
    with SessionLocal() as db:
        first = once(db, f"effect:{key}")
        db.commit()
    if die_after_effect and first:
        os._exit(1)
