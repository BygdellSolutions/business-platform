from datetime import datetime, timezone


def utcnow() -> datetime:
    """The one clock authentication uses (a single seam, so tests can move time)."""
    return datetime.now(timezone.utc)
