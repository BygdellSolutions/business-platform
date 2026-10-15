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
