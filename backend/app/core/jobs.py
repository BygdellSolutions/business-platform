"""Background jobs: the one place business code touches the queue.

PostgreSQL is the queue (Procrastinate's tables and stored functions live in our database), so a job is written INSIDE the
caller's database transaction: commit gives the business change and its job, rollback gives neither. Business code calls
`enqueue` and never imports Procrastinate; the worker (`app.worker`) is the only place that does, so the queue can be
replaced in these two spots.

Rules for every job:
  * It carries ids only (`organization_id` and the record's id), never a document, a file or a secret.
  * Delivery is at-least-once: a worker that dies after doing the work but before telling the queue gets the job run again.
    A job whose effect must not repeat (money, stock, a sent message) wraps that effect in `once(db, key)`.
  * It reads and writes through tenant-scoped queries with the organization id from its own arguments.
"""

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.job_effect import JobEffect

QUEUES = ("maintenance", "pdf", "bulk", "email")

_DEFER = text(
    "SELECT (procrastinate_defer_jobs_v1(ARRAY[ROW(CAST(:queue AS varchar), CAST(:task AS varchar), CAST(:priority AS integer), "
    "CAST(:lock AS text), CAST(:queueing_lock AS text), CAST(:args AS jsonb), CAST(:scheduled_at AS timestamptz))"
    "::procrastinate_job_to_defer_v1]))[1]"
)


def enqueue(
    db: Session,
    task: str,
    *,
    queue: str,
    args: dict[str, Any] | None = None,
    lock: str | None = None,
    queueing_lock: str | None = None,
    scheduled_at: datetime | None = None,
    priority: int = 0,
) -> int:
    """Write a job in the caller's transaction (nothing is committed here) and return its id.

    `lock`: jobs sharing a lock never run at the same time. `queueing_lock`: while a job with this key is waiting, a second
    one is refused (the database raises a unique violation), for work that only needs to be queued once.
    """
    if queue not in QUEUES:
        raise ValueError(f"unknown queue {queue!r}")
    arguments = json.dumps(args or {}, allow_nan=False)
    return db.execute(
        _DEFER,
        {"queue": queue, "task": task, "priority": priority, "lock": lock, "queueing_lock": queueing_lock, "args": arguments, "scheduled_at": scheduled_at},
    ).scalar_one()


def once(db: Session, key: str) -> bool:
    """True the first time `key` is claimed, False every later time. Call it in the SAME transaction as the effect it guards,
    so the claim and the effect commit or roll back together (a crash before the commit leaves neither)."""
    claimed = db.execute(insert(JobEffect).values(key=key).on_conflict_do_nothing().returning(JobEffect.key)).first()
    return claimed is not None


# --- fairness between organizations, and jobs that failed for good ---------------------------------------------------------------

class TooManyJobs(Exception):
    """The organization already has as many jobs of this queue waiting or running as it may (try again when some have finished)."""


def enqueue_for_organization(
    db: Session, task: str, *, queue: str, organization_id: uuid.UUID, args: dict[str, Any] | None = None, max_active: int = 2, **options: Any
) -> int:
    """`enqueue` for work done on behalf of ONE organization, with a cap on how many such jobs of this queue it may have waiting or
    running at once, so a large export or import by one tenant never fills the queue for the others. The organization id travels in
    the job's arguments (and nowhere else: the job reads it from there). Two requests at the same instant cannot both slip under the
    cap: the check runs under a lock on (organization, queue) held until the transaction ends."""
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": f"jobs:{organization_id}:{queue}"})
    active = db.execute(
        text(
            "SELECT count(*) FROM procrastinate_jobs WHERE queue_name = :queue AND args ->> 'organization_id' = :org AND status IN ('todo', 'doing')"
        ),
        {"queue": queue, "org": str(organization_id)},
    ).scalar_one()
    if active >= max_active:
        raise TooManyJobs(f"{active} {queue} jobs are already waiting or running for this organization")
    return enqueue(db, task, queue=queue, args={**(args or {}), "organization_id": str(organization_id)}, **options)


def failed_jobs(db: Session) -> list[dict[str, Any]]:
    """The jobs that failed for good (no retry left), oldest first: what an operator has to look at. Arguments are ids only by rule."""
    rows = db.execute(
        text(
            "SELECT id, queue_name, task_name, attempts, args, scheduled_at FROM procrastinate_jobs WHERE status = 'failed' ORDER BY id"
        )
    ).mappings()
    return [dict(row) for row in rows]


def retry_failed(db: Session, job_id: int) -> bool:
    """Put one failed job back in the queue (by hand, after the cause is fixed). False when there is no such failed job."""
    moved = db.execute(
        text("UPDATE procrastinate_jobs SET status = 'todo', scheduled_at = now() WHERE id = :id AND status = 'failed'"), {"id": job_id}
    ).rowcount
    return moved == 1
