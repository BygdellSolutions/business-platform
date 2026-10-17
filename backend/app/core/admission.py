"""Whole-request admission: bound how many requests the process works on at once, before any of them takes a
database connection.

Without it a burst starts a request per thread, every one of them waits for a pooled connection, and the process
spends its time scheduling waiters instead of finishing work (measured in docs/loadtest-2026-10-13.md). Here at most
`max_running` requests run, at most `max_waiting` more queue for at most `wait_seconds`, and anything beyond that is
refused AT ONCE with a 503 and Retry-After, so the caller can back off while the requests already running finish.

Pure ASGI like the request-context layer (a response is never buffered). It sits INSIDE the internal-secret check, so a
request that would be refused anyway never takes a slot, and inside the request-context layer, so a refusal still gets a
request id, the safe headers and its log line. It belongs to ONE event loop, like the server's (an asyncio semaphore
cannot be waited on from two loops). The limits are per process: with N processes the machine admits N times as
many, and the pool budget has to cover that.
"""

import asyncio
import logging

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core import metrics
from app.core.config import settings
from app.core.internal_auth import EXEMPT_PATHS
from app.core.logging_config import log

RETRY_AFTER_SECONDS = "1"


class AdmissionMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        max_running: int | None = None,
        max_waiting: int | None = None,
        wait_seconds: float | None = None,
    ) -> None:
        self.app = app
        self.max_running = settings.admission_max_running if max_running is None else max_running
        self.max_waiting = settings.admission_max_waiting if max_waiting is None else max_waiting
        self.wait_seconds = settings.admission_wait_seconds if wait_seconds is None else wait_seconds
        self._slots = asyncio.Semaphore(self.max_running)
        self.running = 0
        self.waiting = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Probes must answer while the process is full: they have their own timeouts and single-flight guards.
        if scope["type"] != "http" or scope.get("path") in EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        reason = await self._admit()
        metrics.gauge("admission_waiting", self.waiting)
        if reason is not None:
            metrics.count(f"admission_rejected_{reason}")
            log(
                logging.WARNING,
                "admission_rejected",
                reason=reason,
                running=self.running,
                waiting=self.waiting,
                request_id=scope.get("bp_request_id", ""),
            )
            response = JSONResponse(
                {"detail": {"code": "overloaded", "message": "The service is busy. Try again in a moment."}},
                status_code=503,
                headers={"Retry-After": RETRY_AFTER_SECONDS, "cache-control": "no-store"},
            )
            await response(scope, receive, send)
            return

        self.running += 1
        metrics.gauge("admission_running", self.running)
        try:
            await self.app(scope, receive, send)
        finally:
            # A failed, cancelled or disconnected request gives its slot back like any other.
            self.running -= 1
            metrics.gauge("admission_running", self.running)
            self._slots.release()

    async def _admit(self) -> str | None:
        """Take a slot (None) or say why not: "queue_full" or "wait_timeout"."""
        if not self._slots.locked():
            await self._slots.acquire()  # a slot is free: no waiting, and no other task can take it in between
            return None
        if self.waiting >= self.max_waiting:
            return "queue_full"
        self.waiting += 1
        try:
            await asyncio.wait_for(self._slots.acquire(), self.wait_seconds)
        except TimeoutError:
            return "wait_timeout"
        finally:
            self.waiting -= 1
        return None
