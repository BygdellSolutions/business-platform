"""Process-local counters and gauges for the OPERATOR: how busy this process is and how it is refusing work.

Deliberately small and dependency-free. Everything here is aggregate and tenant-free (counts of requests by status class,
refusals, the pool and admission levels): no path, user, organization or request content is ever recorded, so this can
neither leak tenant data nor grow without bound. Counters are per process and restart at zero; a collector that scrapes
`GET /internal/metrics` (it needs the BFF secret like every internal route) adds processes together and takes rates.
"""

import threading
from collections import Counter


# Request time buckets in milliseconds: how many requests finished faster than each edge (the last one is the rest).
_BUCKETS_MS = (100, 500, 1000, 5000)

_lock = threading.Lock()
_counts: Counter[str] = Counter()
_gauges: dict[str, int] = {}


def count(name: str, amount: int = 1) -> None:
    with _lock:
        _counts[name] += amount


def gauge(name: str, value: int) -> None:
    with _lock:
        _gauges[name] = value


def observe_request(status: int, duration_ms: float) -> None:
    """One finished request: its status class and which time bucket it fell in."""
    bucket = next((f"lt_{edge}ms" for edge in _BUCKETS_MS if duration_ms < edge), f"ge_{_BUCKETS_MS[-1]}ms")
    with _lock:
        _counts["requests_total"] += 1
        _counts[f"requests_{status // 100}xx"] += 1
        _counts[f"request_time_{bucket}"] += 1


def snapshot() -> dict[str, object]:
    from app.core import db  # imported here: counting a request must not need a database configuration

    pool = db.engine.pool
    with _lock:
        counters = dict(_counts)
        gauges = dict(_gauges)
    return {
        "counters": counters,
        "gauges": gauges,
        "pool": {
            "size": pool.size(),
            "checked_out": pool.checkedout(),
            "checked_in": pool.checkedin(),
            "overflow": pool.overflow(),
        },
    }


def reset() -> None:
    """For tests only."""
    with _lock:
        _counts.clear()
        _gauges.clear()
