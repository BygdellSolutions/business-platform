"""EXPERIMENT ONLY: bound whole-request concurrency before sync dependencies acquire database connections.

Mounted into disposable load-test containers; this is not wired into the application or deployment.
"""
import asyncio
import os

from app.main import app as original_app


class Admission:
    def __init__(self, app, concurrency=12, waiting=48, timeout=1.0):
        self.app = app
        self.slots = asyncio.BoundedSemaphore(concurrency)
        self.waiting = 0
        self.max_waiting = waiting
        self.timeout = timeout

    async def busy(self, send):
        await send(dict(type='http.response.start', status=503, headers=[(b'content-type', b'application/json'), (b'retry-after', b'1'), (b'cache-control', b'no-store')]))
        await send(dict(type='http.response.body', body=b'{"detail":"Temporary capacity limit"}'))

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('path', '').startswith('/health'):
            return await self.app(scope, receive, send)
        if self.waiting >= self.max_waiting:
            return await self.busy(send)
        self.waiting += 1
        try:
            try:
                await asyncio.wait_for(self.slots.acquire(), self.timeout)
            except TimeoutError:
                return await self.busy(send)
        finally:
            self.waiting -= 1
        try:
            await self.app(scope, receive, send)
        finally:
            self.slots.release()


app = Admission(original_app, concurrency=int(os.environ.get('LOAD_ADMISSION_CONCURRENT', '12')))
