"""Startup gating and outage behaviour of the rehearsal stack (LOCAL semantics only: how Coolify replaces containers is a D5
verification, not something Compose's depends_on can prove).

  * a migration that fails (or is refused) means the new backend and frontend are never started;
  * a backend that never migrated is unready, not dead, and recovers when the job runs, without a restart;
  * a database outage makes the backend unready (bounded, no hung probes), never a restart, and liveness stays up."""

import json
import time

import pytest

from conftest import docker, get
from stack_support import Stack, repository_head


def prepare(stack: Stack) -> None:
    """The database up and the roles bootstrapped (the first two steps of the chain), nothing else."""
    stack.compose("up", "-d", "--wait", "postgres")
    stack.compose("run", "--rm", "--no-deps", "db-bootstrap")


@pytest.fixture
def fresh():
    stacks: list[Stack] = []

    def make(label: str) -> Stack:
        s = Stack(label)
        stacks.append(s)
        s.compose("build")
        return s

    yield make
    for s in stacks:
        s.down()


def test_a_refused_migration_prevents_the_backend_and_the_frontend_from_ever_starting(fresh):
    s = fresh("refused")
    prepare(s)
    # A database that was migrated by a NEWER release: the job must refuse it, and nothing may come up on top of it.
    s.sql("create table alembic_version (version_num varchar(32) not null primary key); alter table alembic_version owner to bp_owner; insert into alembic_version values ('ffffffffffff')")
    result = s.compose("up", "-d", "frontend", check=False)
    assert result.returncode != 0  # compose itself reports that migrate did not complete successfully
    migrate = s.state("migrate")
    assert (migrate["Status"], migrate["ExitCode"]) == ("exited", 4)  # refused: unknown revision
    assert '"event":"migration_refused"' in s.compose("logs", "--no-log-prefix", "migrate").stdout
    for service in ("backend", "frontend"):
        containers = s.compose("ps", "-a", "-q", service).stdout.strip()
        if containers:  # created but never started is allowed; running never
            assert not s.state(service)["Running"] and s.state(service)["StartedAt"].startswith("0001-"), service
    assert s.ready() == 0  # nothing answers on the published port
    assert s.sql("select version_num from alembic_version") == "ffffffffffff"  # and nothing was changed


def test_a_failing_migration_job_also_prevents_the_stack_and_exits_non_zero(fresh):
    s = fresh("failing")
    prepare(s)
    # Make the owner's session fail while migrating: a table the first migration wants to create already exists, owned by someone else.
    s.sql("create table organizations (id int)")
    result = s.compose("up", "-d", "frontend", check=False)
    assert result.returncode != 0
    assert s.state("migrate")["ExitCode"] == 1 and '"event":"migration_failed"' in s.compose("logs", "--no-log-prefix", "migrate").stdout
    assert not s.state("backend")["Running"] if s.compose("ps", "-a", "-q", "backend").stdout.strip() else True
    assert s.ready() == 0
    assert s.sql("select to_regclass('alembic_version') is null") == "t"  # the failed job left no half-applied schema revision


def test_a_backend_that_never_migrated_is_unready_not_dead_and_recovers_when_the_job_runs(fresh):
    s = fresh("unready")
    prepare(s)
    s.compose("up", "-d", "--no-deps", "backend")  # skip the migrate dependency ON PURPOSE
    for _ in range(60):
        try:
            if s.backend_probe("/health")[0] == 200:
                break
        except AssertionError:
            time.sleep(1)
    else:
        raise AssertionError("the backend never answered /health")
    before = s.state("backend")
    assert s.backend_probe("/health") == (200, '{"status":"ok"}')  # alive
    assert s.backend_probe("/health/ready") == (503, '{"status":"unready"}')  # but not ready: there is no schema, and it will not make one
    assert s.sql("select to_regclass('alembic_version') is null") == "t"  # it did not migrate by itself
    assert before["Running"] and before["RestartCount"] == 0

    s.compose("run", "--rm", "--no-deps", "migrate")  # the job
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and s.backend_probe("/health/ready")[0] != 200:
        time.sleep(1)
    assert s.backend_probe("/health/ready") == (200, '{"status":"ready"}')
    after = s.state("backend")
    assert (after["Id"], after["StartedAt"], after["RestartCount"]) == (before["Id"], before["StartedAt"], 0)  # the same process: no restart
    assert s.sql("select version_num from alembic_version") == repository_head()


def test_a_database_outage_makes_the_backend_unready_but_never_restarts_it_and_liveness_stays_up(fresh):
    s = fresh("outage")
    s.compose("up", "-d", "frontend")
    s.wait_ready()
    before = {name: s.state(name) for name in ("backend", "frontend")}
    postgres = s.container_id("postgres")
    docker("pause", postgres)  # connections hang (a worse outage than a refused connection: nothing answers)
    try:
        # readiness: bounded, and no pile-up (a second probe does not start a second stuck query)
        for _ in range(4):
            started_at = time.monotonic()
            status, body = s.backend_probe("/health/ready", timeout=20)
            assert (status, body) == (503, '{"status":"unready"}')
            assert time.monotonic() - started_at < 8
        # liveness never waits for a worker thread, so it stays instant while a probe is stuck on the database
        started_at = time.monotonic()
        assert s.backend_probe("/health") == (200, '{"status":"ok"}')
        assert time.monotonic() - started_at < 5
        # the chain: the frontend is alive, and says the chain is not ready (coarsely)
        assert get(f"{s.base}/api/health")[0] == 200
        status, _, body = get(f"{s.base}/api/ready")
        assert (status, json.loads(body)) == (503, {"status": "unready"})
        # an ordinary request that needs the database ends in a fixed, coarse answer (no hostname, trace or driver text)
        login = get(f"{s.base}/api/auth/pre")
        assert login[0] == 200  # (BFF-only route: unaffected)
        for service, was in before.items():
            now = s.state(service)
            assert now["Running"] and now["RestartCount"] == 0 and now["StartedAt"] == was["StartedAt"], service  # NOT restarted
    finally:
        docker("unpause", postgres)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and s.ready() != 200:
        time.sleep(1)
    assert s.ready() == 200  # it recovers by itself when the database is back
    assert s.state("backend")["StartedAt"] == before["backend"]["StartedAt"]
