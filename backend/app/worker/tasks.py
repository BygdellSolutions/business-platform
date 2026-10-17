"""The tasks the worker runs. Each one is idempotent (delivery is at-least-once, see app/core/jobs.py)."""

import logging

from procrastinate import JobContext

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.jobs import failed_jobs
from app.scripts.admin import purge
from app.worker.queue import app

log = logging.getLogger("app.worker")


@app.periodic(cron="17 3 * * *")  # every night, 03:17
@app.task(name="maintenance.purge", queue="maintenance", queueing_lock="maintenance.purge")
def purge_expired_records(timestamp: int) -> None:
    """Delete expired sessions, setup tokens and security events past their retention (what `admin purge` does by hand)."""
    with SessionLocal() as db:
        counts = purge(db)
    log.info("purge finished", extra={"counts": counts})


@app.periodic(cron="*/5 * * * *")
@app.task(name="maintenance.recover_stalled_jobs", queue="maintenance", queueing_lock="maintenance.recover_stalled_jobs", pass_context=True)
async def recover_stalled_jobs(context: JobContext, timestamp: int) -> None:
    """Give the jobs of a worker that died back to the queue. A job is only ever in "doing" while some worker reports it, so
    one whose worker stopped reporting is safe to run again (its effects are guarded by `jobs.once`)."""
    manager = context.app.job_manager
    for job in await manager.get_stalled_jobs(seconds_since_heartbeat=settings.worker_stalled_seconds):
        if job.id == context.job.id:
            continue  # this very job (a worker that has just started has not reported yet)
        await manager.retry_job(job)
        log.warning("gave a stalled job back to the queue", extra={"job_id": job.id, "task": job.task_name})


@app.periodic(cron="43 3 * * *")
@app.task(name="maintenance.remove_old_jobs", queue="maintenance", queueing_lock="maintenance.remove_old_jobs", pass_context=True)
async def remove_old_jobs(context: JobContext, timestamp: int) -> None:
    """Finished jobs are kept a week for inspection; failed ones stay until someone looks at them."""
    await context.app.job_manager.delete_old_jobs(nb_hours=24 * 7, include_failed=False)


@app.periodic(cron="*/10 * * * *")
@app.task(name="maintenance.report_failed_jobs", queue="maintenance", queueing_lock="maintenance.report_failed_jobs")
def report_failed_jobs(timestamp: int) -> None:
    """Say, at ERROR level (so the platform's log alerting sees it), that jobs failed for good and which. Until email exists this and
    `admin failed-jobs` are how it is noticed; a failed job stays until an operator retries it (`admin retry-job`) or it is removed."""
    with SessionLocal() as db:
        failed = failed_jobs(db)
    if failed:
        log.error("jobs failed for good", extra={"count": len(failed), "jobs": [(job["id"], job["task_name"]) for job in failed[:20]]})

