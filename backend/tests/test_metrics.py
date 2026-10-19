"""Operator metrics: aggregate, tenant-free counters; only readable with the BFF secret; and they count what they claim."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.core import db, metrics
from app.core.admission import AdmissionMiddleware
from app.core.config import settings
from app.main import app
from tests.test_admission import Gate, call, settle


@pytest.fixture(autouse=True)
def fresh_counters():
    metrics.reset()
    yield
    metrics.reset()


def test_each_finished_request_is_counted_by_status_class_and_time_bucket():
    metrics.observe_request(200, 12.0)
    metrics.observe_request(201, 150.0)
    metrics.observe_request(404, 700.0)
    metrics.observe_request(503, 6000.0)

    counters = metrics.snapshot()["counters"]
    assert counters["requests_total"] == 4
    assert (counters["requests_2xx"], counters["requests_4xx"], counters["requests_5xx"]) == (2, 1, 1)
    assert [counters[f"request_time_{b}"] for b in ("lt_100ms", "lt_500ms", "lt_1000ms", "ge_5000ms")] == [1, 1, 1, 1]


def test_a_real_request_through_the_application_is_counted():
    TestClient(app).get("/health")

    counters = metrics.snapshot()["counters"]
    assert counters["requests_total"] == 1 and counters["requests_2xx"] == 1


def test_admission_refusals_and_levels_are_counted():
    async def scenario():
        gate = Gate()
        guard = AdmissionMiddleware(gate, max_running=1, max_waiting=0, wait_seconds=0.1)
        first = asyncio.create_task(call(guard))
        await settle()
        assert metrics.snapshot()["gauges"]["admission_running"] == 1
        await call(guard)  # refused: no waiting room
        gate.release()
        await first

    asyncio.run(asyncio.wait_for(scenario(), 10))
    snapshot = metrics.snapshot()
    assert snapshot["counters"]["admission_rejected_queue_full"] == 1
    assert snapshot["gauges"]["admission_running"] == 0


def test_a_saturated_pool_is_counted():
    def exhausted():
        raise PoolTimeoutError("QueuePool limit reached")
        yield  # pragma: no cover

    app.dependency_overrides[db.get_db] = exhausted
    try:
        TestClient(app, raise_server_exceptions=False).get("/api/customers")
    finally:
        app.dependency_overrides.pop(db.get_db, None)
    assert metrics.snapshot()["counters"]["pool_exhausted"] == 1


def test_the_snapshot_shows_the_pool_and_holds_no_request_content():
    TestClient(app).get("/health")
    snapshot = metrics.snapshot()

    assert set(snapshot["pool"]) == {"size", "checked_out", "checked_in", "overflow"}
    text = json.dumps(snapshot)
    assert "/health" not in text and "customers" not in text  # counts only: no path, user or organization


def test_the_endpoint_needs_the_internal_secret_when_one_is_configured(monkeypatch):
    secret = "s" * 40
    monkeypatch.setattr(settings, "bff_internal_secret", SecretStr(secret))
    client = TestClient(app)

    assert client.get("/internal/metrics").status_code == 403
    allowed = client.get("/internal/metrics", headers={settings.bff_internal_header: secret})
    assert allowed.status_code == 200
    assert allowed.headers["cache-control"] == "no-store"
    assert set(allowed.json()) == {"counters", "gauges", "pool"}
