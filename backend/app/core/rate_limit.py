"""Request budgets for signed-in traffic: per user, per organization, and a tighter one for expensive endpoints.

Login abuse is already throttled from the database (app/core/throttle.py). This protects the rest: one person (or one runaway
script holding a valid session) cannot use up the process for everybody, and one organization cannot starve the others.

shortcut: the counters live in THIS process (a sliding window per key, bounded by `MAX_KEYS`). With N API processes a person may
use up to N times the budget, and a restart empties the counters. That is enough to stop accidental and naive abuse and costs
no database round trip per request; move the counters to PostgreSQL (like login throttling) when a hard per-tenant guarantee
or a billing limit needs it.

A refused request is NOT recorded, so a block ends as soon as the person's earlier requests leave the window (they cannot keep
themselves blocked by retrying).
"""

import threading
import time
from collections import OrderedDict, deque

from fastapi import Depends, HTTPException, status

from app.core import metrics
from app.core.config import settings
from app.core.tenant import TenantContext, get_tenant_context

WINDOW_SECONDS = 60.0
MAX_KEYS = 20_000  # least recently used keys are forgotten first: memory stays bounded whatever the traffic

clock = time.monotonic  # a seam: tests replace it instead of sleeping


class SlidingWindow:
    def __init__(self, max_keys: int = MAX_KEYS) -> None:
        self._max_keys = max_keys
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window: float = WINDOW_SECONDS) -> int | None:
        """Count one request for `key`. None when it is allowed, otherwise the whole seconds until it would be."""
        now = clock()
        with self._lock:
            recent = self._hits.get(key)
            if recent is None:
                recent = self._hits[key] = deque()
                if len(self._hits) > self._max_keys:
                    self._hits.popitem(last=False)
            else:
                self._hits.move_to_end(key)
            while recent and recent[0] <= now - window:
                recent.popleft()
            if len(recent) >= limit:
                return max(1, int(recent[0] + window - now) + 1)
            recent.append(now)
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


_windows = {"user": SlidingWindow(), "org": SlidingWindow(), "heavy": SlidingWindow(), "export": SlidingWindow(), "support": SlidingWindow()}


def enforce(bucket: str, key: str, limit: int, window: float = WINDOW_SECONDS) -> None:
    retry_after = _windows[bucket].hit(key, limit, window)
    if retry_after is not None:
        metrics.count(f"rate_limited_{bucket}")
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "rate_limited", "message": "Too many requests. Wait a moment and try again."},
            headers={"Retry-After": str(retry_after)},
        )


def tenant_request(ctx: TenantContext) -> None:
    """Every authenticated tenant request: the person's budget first, then the organization's."""
    enforce("user", f"u:{ctx.user.id}", settings.rate_limit_user_per_minute)
    enforce("org", f"o:{ctx.organization_id}", settings.rate_limit_org_per_minute)


def heavy(ctx: TenantContext = Depends(get_tenant_context)) -> None:
    """An expensive endpoint (a PDF, the dashboard summary): a tighter per-person budget on top of the general ones."""
    enforce("heavy", f"u:{ctx.user.id}", settings.rate_limit_heavy_per_minute)


def export_copy(ctx: TenantContext) -> None:
    """A complete copy of an organization's data is the most expensive thing a person can ask for: a few per hour per organization."""
    enforce("export", f"o:{ctx.organization_id}", settings.rate_limit_export_per_hour, window=3600.0)


def reset() -> None:
    """For tests only."""
    for window in _windows.values():
        window.reset()
