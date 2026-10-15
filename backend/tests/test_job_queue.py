"""The job queue with REAL commits and a REAL worker process (the rest of the suite runs in one rolled-back transaction, which
can prove none of this). What is proven:

  - a job is written in the caller's transaction: commit gives the business change AND its job, rollback gives NEITHER;
  - a worker in another process runs the job;
  - a worker that dies after the effect but before the queue hears of it, then the job being delivered again, applies the
    effect ONCE (`once`), and the job finishes;
  - the queue refuses unknown queues and a second waiting job with the same queueing lock.
"""

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.db import SessionLocal
from app.core.jobs import enqueue, once

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def key():
    key = f"test-{uuid.uuid4()}"
    yield key
    with SessionLocal() as db:
        db.execute(text("DELETE FROM job_effects WHERE key LIKE :k"), {"k": f"%{key}%"})
        db.execute(text("DELETE FROM procrastinate_jobs WHERE args->>'key' = :k OR queueing_lock = :k"), {"k": key})
        db.commit()


def jobs_of(key: str) -> list[tuple[str, str]]:
    with SessionLocal() as db:
        return [tuple(row) for row in db.execute(text("SELECT task_name, status::text FROM procrastinate_jobs WHERE args->>'key' = :k ORDER BY id"), {"k": key})]


def effects_of(key: str) -> int:
    with SessionLocal() as db:
        return db.execute(text("SELECT count(*) FROM job_effects WHERE key = :k"), {"k": f"effect:{key}"}).scalar_one()


def worker(*extra_env: tuple[str, str], seconds: int = 60) -> subprocess.CompletedProcess:
    env = {**os.environ, "WORKER_TASK_MODULES": "app.worker.tasks,tests.worker_probe", "WORKER_STALLED_SECONDS": "1", "WORKER_HEARTBEAT_SECONDS": "0.5", **dict(extra_env)}
    return subprocess.run([sys.executable, "-m", "app.worker", "--drain"], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=seconds)


def test_commit_gives_the_change_and_its_job(key):
    with SessionLocal() as db:
        assert once(db, f"effect:{key}")
        enqueue(db, "probe.apply", queue="maintenance", args={"key": key})
        db.commit()
    assert effects_of(key) == 1
    assert jobs_of(key) == [("probe.apply", "todo")]


def test_rollback_gives_neither(key):
    with SessionLocal() as db:
        assert once(db, f"effect:{key}")
        enqueue(db, "probe.apply", queue="maintenance", args={"key": key})
        db.rollback()
    assert effects_of(key) == 0
    assert jobs_of(key) == []


def test_once_is_true_the_first_time_only(key):
    with SessionLocal() as db:
        assert once(db, key) is True
        assert once(db, key) is False
        db.rollback()
        assert once(db, key) is True  # a rolled-back claim claims nothing
        db.rollback()


def test_unknown_queue_and_a_second_waiting_job_with_the_same_queueing_lock_are_refused(key):
    with SessionLocal() as db:
        with pytest.raises(ValueError):
            enqueue(db, "probe.apply", queue="nonsense", args={"key": key})
        enqueue(db, "probe.apply", queue="maintenance", args={"key": key}, queueing_lock=key)
        with pytest.raises(IntegrityError):
            enqueue(db, "probe.apply", queue="maintenance", args={"key": key}, queueing_lock=key)
        db.rollback()


def test_a_worker_in_another_process_runs_the_job(key):
    with SessionLocal() as db:
        enqueue(db, "probe.apply", queue="maintenance", args={"key": key})
        db.commit()
    done = worker()
    assert done.returncode == 0, done.stderr[-2000:]
    assert jobs_of(key) == [("probe.apply", "succeeded")]
    assert effects_of(key) == 1


def test_a_worker_that_dies_after_the_effect_does_not_apply_it_twice(key):
    with SessionLocal() as db:
        enqueue(db, "probe.apply", queue="maintenance", args={"key": key, "die_after_effect": True})
        db.commit()
    worker()  # dies inside the job: the effect is committed, the queue was never told
    assert effects_of(key) == 1
    assert jobs_of(key) == [("probe.apply", "doing")]  # stuck: nobody reports it any more

    time.sleep(2)  # longer than the stall limit (1 s): its worker has stopped reporting
    with SessionLocal() as db:  # the periodic recovery job, asked for now instead of at the next five-minute mark
        enqueue(db, "maintenance.recover_stalled_jobs", queue="maintenance", args={"timestamp": 0}, queueing_lock=f"{key}-r")
        db.commit()
    worker()  # runs the recovery job, which puts the dead worker's job back in the queue ...
    done = worker()  # ... and the next worker delivers it again
    assert done.returncode == 0, done.stderr[-2000:]

    assert jobs_of(key) == [("probe.apply", "succeeded")], done.stderr[-3000:]  # delivered again and finished ...
    assert effects_of(key) == 1  # ... without a second effect
    with SessionLocal() as db:
        db.execute(text("DELETE FROM procrastinate_jobs WHERE queueing_lock = :k"), {"k": f"{key}-r"})
        db.commit()
