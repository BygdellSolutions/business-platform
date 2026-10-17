"""Whole-request admission: bounded running and waiting, a safe immediate 503, and slots that always come back.

These drive the ASGI layer directly with gated requests (no database), so every ordering can be forced: a request is
held inside the application until the test releases it.
"""

import asyncio
import json

import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.core.admission import AdmissionMiddleware
from app.core.config import Settings, settings
from app.main import configure_middleware


class Gate:
    """An ASGI application whose requests wait for `release()`; it records how many were inside at once."""

    def __init__(self) -> None:
        self.event = asyncio.Event()
        self.inside = 0
        self.peak = 0
        self.entered = 0

    def release(self) -> None:
        self.event.set()

    async def __call__(self, scope, receive, send) -> None:
        self.inside += 1
        self.entered += 1
        self.peak = max(self.peak, self.inside)
        try:
            await self.event.wait()
            await PlainTextResponse("ok")(scope, receive, send)
        finally:
            self.inside -= 1


async def call(app, path: str = "/x"):
    """One request through `app`: (status, headers as a dict, body bytes)."""
    scope = {"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b"", "scheme": "http", "server": ("t", 80)}
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    return start["status"], headers, b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


async def settle() -> None:
    """Let every ready task run up to its next wait."""
    for _ in range(5):
        await asyncio.sleep(0)


def admission(app, running=1, waiting=0, wait=0.2) -> AdmissionMiddleware:
    return AdmissionMiddleware(app, max_running=running, max_waiting=waiting, wait_seconds=wait)


def run(coroutine):
    """A scenario that hangs (a guard that never lets go) must FAIL, not stall the suite."""
    return asyncio.run(asyncio.wait_for(coroutine, 10))


def test_no_more_than_the_limit_runs_at_once_and_the_rest_are_refused_at_once():
    async def scenario():
        gate = Gate()
        guard = admission(gate, running=2, waiting=0)
        tasks = [asyncio.create_task(call(guard)) for _ in range(2)]
        await settle()
        assert gate.inside == 2

        status, headers, body = await call(guard)  # a third while two run, with no waiting room
        assert status == 503
        assert gate.entered == 2  # the refused request never reached the application

        gate.release()
        assert [r[0] for r in await asyncio.gather(*tasks)] == [200, 200]
        assert gate.peak == 2
        return headers, body

    headers, body = run(scenario())
    assert headers["retry-after"] == "1"
    assert headers["cache-control"] == "no-store"
    assert json.loads(body) == {"detail": {"code": "overloaded", "message": "The service is busy. Try again in a moment."}}


def test_a_waiting_request_is_admitted_when_a_slot_frees_within_the_wait():
    async def scenario():
        gate = Gate()
        guard = admission(gate, running=1, waiting=1, wait=2.0)
        first = asyncio.create_task(call(guard))
        await settle()
        second = asyncio.create_task(call(guard))
        await settle()
        assert guard.waiting == 1 and gate.entered == 1

        gate.release()
        return (await first)[0], (await second)[0], guard.waiting, guard.running

    assert run(scenario()) == (200, 200, 0, 0)


def test_the_queue_is_bounded_and_a_request_past_it_is_refused_without_waiting():
    async def scenario():
        gate = Gate()
        guard = admission(gate, running=1, waiting=1, wait=5.0)
        first = asyncio.create_task(call(guard))
        await settle()
        second = asyncio.create_task(call(guard))  # takes the one waiting place
        await settle()

        started = asyncio.get_running_loop().time()
        status = (await call(guard))[0]  # the third finds no room: refused now, not after the 5 s wait
        elapsed = asyncio.get_running_loop().time() - started

        gate.release()
        await asyncio.gather(first, second)
        return status, elapsed

    status, elapsed = run(scenario())
    assert status == 503
    assert elapsed < 1.0


def test_a_request_that_waits_too_long_is_refused_and_the_queue_empties():
    async def scenario():
        gate = Gate()
        guard = admission(gate, running=1, waiting=2, wait=0.05)
        first = asyncio.create_task(call(guard))
        await settle()
        status = (await call(guard))[0]  # waits 50 ms for a slot that never frees
        assert guard.waiting == 0
        gate.release()
        await first
        return status

    assert run(scenario()) == 503


def test_health_probes_are_never_refused_when_the_process_is_full():
    async def scenario():
        gate = Gate()
        guard = admission(gate, running=1, waiting=0)
        busy = asyncio.create_task(call(guard))
        await settle()

        async def probe_app(scope, receive, send):
            await PlainTextResponse("alive")(scope, receive, send)

        probes = admission(probe_app, running=1, waiting=0)
        probes._slots = guard._slots  # the same, full, set of slots
        results = [(await call(probes, path))[0] for path in ("/health", "/health/ready")]
        refused = (await call(probes, "/customers"))[0]
        gate.release()
        await busy
        return results, refused

    assert run(scenario()) == ([200, 200], 503)


def test_a_failing_request_gives_its_slot_back():
    async def scenario():
        async def broken(scope, receive, send):
            raise RuntimeError("boom")

        guard = admission(broken, running=1, waiting=0)
        for _ in range(3):  # a leaked slot would turn the second attempt into a 503
            with pytest.raises(RuntimeError):
                await call(guard)
        return guard.running

    assert run(scenario()) == 0


def test_a_cancelled_running_request_gives_its_slot_back():
    async def scenario():
        gate = Gate()
        guard = admission(gate, running=1, waiting=0)
        task = asyncio.create_task(call(guard))
        await settle()
        task.cancel()  # a client that went away
        with pytest.raises(asyncio.CancelledError):
            await task
        gate.release()
        return (await call(guard))[0], guard.running

    assert run(scenario()) == (200, 0)


def test_a_cancelled_waiting_request_does_not_take_or_keep_a_place():
    async def scenario():
        gate = Gate()
        guard = admission(gate, running=1, waiting=1, wait=5.0)
        first = asyncio.create_task(call(guard))
        await settle()
        waiter = asyncio.create_task(call(guard))
        await settle()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert guard.waiting == 0

        later = asyncio.create_task(call(guard))  # the place the cancelled one held is free again
        await settle()
        assert guard.waiting == 1
        gate.release()
        return (await first)[0], (await later)[0], guard.running

    assert run(scenario()) == (200, 200, 0)


def test_a_burst_is_bounded_and_service_continues_afterwards_without_a_restart():
    async def scenario():
        gate = Gate()
        guard = admission(gate, running=4, waiting=6, wait=2.0)
        burst = [asyncio.create_task(call(guard)) for _ in range(100)]
        await settle()
        assert gate.inside == 4 and guard.waiting == 6  # 90 of the 100 were refused while the rest held the places

        gate.release()
        statuses = [r[0] for r in await asyncio.gather(*burst)]
        after = (await call(guard))[0]
        return statuses, after, gate.peak, guard.running, guard.waiting

    statuses, after, peak, running, waiting = run(scenario())
    assert statuses.count(200) == 10 and statuses.count(503) == 90
    assert (after, peak, running, waiting) == (200, 4, 0, 0)


def test_through_the_real_stack_a_refusal_still_has_a_request_id_and_a_log_line(monkeypatch):
    monkeypatch.setattr(settings, "bff_internal_secret", None)
    monkeypatch.setattr(settings, "admission_max_running", 1)
    monkeypatch.setattr(settings, "admission_max_waiting", 0)
    gate = Gate()

    async def held(request):
        await gate.event.wait()
        return PlainTextResponse("ok")

    application = FastAPI(routes=[Route("/held", held)])
    configure_middleware(application, [])

    async def scenario():
        first = asyncio.create_task(call(application, "/held"))
        await settle()
        refused = await call(application, "/held")
        gate.release()
        await first
        return refused

    status, headers, _ = run(scenario())
    assert status == 503
    assert len(headers["x-request-id"]) >= 16  # the request-context layer is OUTSIDE admission
    assert headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("name,value", [("admission_max_running", 0), ("admission_max_waiting", -1), ("admission_wait_seconds", 0)])
def test_the_limits_are_validated(name, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="postgresql+psycopg://x", **{name: value})
