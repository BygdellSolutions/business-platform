"""`python -m app.worker`: run a worker. Stateless; run as many as needed (another container of the same image).

    --drain   run what is queued now and exit (tests, one-off catch-up) instead of waiting for work
"""

import argparse
import asyncio
import logging
import sys

from app.core.config import settings
from app.worker.queue import app


async def _run(drain: bool) -> None:
    async with app.open_async():
        await app.run_worker_async(
            queues=[name.strip() for name in settings.worker_queues.split(",") if name.strip()],
            concurrency=settings.worker_concurrency,
            wait=not drain,
            update_heartbeat_interval=settings.worker_heartbeat_seconds,
            stalled_worker_timeout=settings.worker_stalled_seconds,
            delete_jobs="never",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.worker")
    parser.add_argument("--drain", action="store_true", help="exit when the queue is empty")
    options = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(_run(options.drain), loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
