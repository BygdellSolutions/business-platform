"""Fairness between organizations in the job queue, and what an operator sees when a job has failed for good (real commits, a real worker)."""

import uuid

import pytest
from sqlalchemy import text

from app.core.db import SessionLocal
from app.core.jobs import TooManyJobs, enqueue_for_organization, failed_jobs, retry_failed
from app.scripts import admin
from tests.test_job_queue import jobs_of, worker


@pytest.fixture
def tag():
    value = f"limits-{uuid.uuid4()}"
    yield value
    with SessionLocal() as db:
        db.execute(text("DELETE FROM job_effects WHERE key LIKE :k"), {"k": f"%{value}%"})
        db.execute(text("DELETE FROM procrastinate_jobs WHERE args->>'key' = :k"), {"k": value})
        db.commit()


def _bulk(db, org, tag, cap=2):
    return enqueue_for_organization(db, "probe.apply", queue="bulk", organization_id=org, args={"key": tag}, max_active=cap)


def test_an_organization_cannot_fill_the_queue_for_everyone_else(tag):
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    with SessionLocal() as db:
        _bulk(db, mine, tag)
        _bulk(db, mine, tag)
        with pytest.raises(TooManyJobs):
            _bulk(db, mine, tag)  # a third while two are waiting
        _bulk(db, theirs, tag)  # another organization is not held up by it
        db.commit()
    assert len(jobs_of(tag)) == 3


def test_a_finished_job_makes_room_again(tag):
    org = uuid.uuid4()
    with SessionLocal() as db:
        _bulk(db, org, tag, cap=1)
        db.commit()
    with SessionLocal() as db:
        with pytest.raises(TooManyJobs):
            _bulk(db, org, tag, cap=1)
    with SessionLocal() as db:  # the job is done (finished jobs do not count)
        db.execute(text("UPDATE procrastinate_jobs SET status = 'succeeded' WHERE args->>'key' = :k"), {"k": tag})
        db.commit()
    with SessionLocal() as db:
        _bulk(db, org, tag, cap=1)
        db.commit()


def test_a_job_that_is_refused_by_the_cap_leaves_nothing_behind_and_the_cap_ignores_other_queues(tag):
    org = uuid.uuid4()
    with SessionLocal() as db:
        _bulk(db, org, tag, cap=1)
        with pytest.raises(TooManyJobs):
            _bulk(db, org, tag, cap=1)
        db.rollback()  # the whole transaction is undone, the first job too
    assert jobs_of(tag) == []
    with SessionLocal() as db:
        enqueue_for_organization(db, "probe.apply", queue="maintenance", organization_id=org, args={"key": tag}, max_active=1)
        enqueue_for_organization(db, "probe.apply", queue="pdf", organization_id=org, args={"key": tag}, max_active=1)  # another queue, its own cap
        db.commit()


def test_a_job_that_failed_for_good_is_listed_and_can_be_put_back_by_hand(tag):
    with SessionLocal() as db:
        from app.core.jobs import enqueue

        enqueue(db, "probe.fail_once", queue="maintenance", args={"key": tag})
        db.commit()
    done = worker()
    assert done.returncode == 0, done.stderr[-1500:]
    assert jobs_of(tag) == [("probe.fail_once", "failed")]
    with SessionLocal() as db:
        listed = [job for job in failed_jobs(db) if job["args"].get("key") == tag]
        assert [(job["task_name"], job["queue_name"]) for job in listed] == [("probe.fail_once", "maintenance")]
        assert any(str(listed[0]["id"]) in line and "probe.fail_once" in line for line in admin.failed_jobs_report(db))
        assert retry_failed(db, 999_999_999) is False  # no such failed job
        assert retry_failed(db, listed[0]["id"]) is True
        db.commit()
    worker()
    assert jobs_of(tag) == [("probe.fail_once", "succeeded")]  # the fix worked this time
    with SessionLocal() as db:
        assert not [job for job in failed_jobs(db) if job["args"].get("key") == tag]
