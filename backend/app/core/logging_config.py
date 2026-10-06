"""The smallest useful production logging policy: one JSON object per line on stdout.

A request line carries a timestamp, level, service, request id, method, path WITHOUT the query string, status and
duration, and nothing else: no headers, no cookies, no bodies, no parameters. Application events are a short fixed
name plus coarse fields (a reason code, an exception CLASS name), never an exception message that could quote a
credential or a connection string. Uvicorn's own access log is silenced (it prints the full request target, query
string included, and would duplicate the safe line).
"""

import json
import logging
import sys
from datetime import datetime, timezone

LOGGER_NAME = "bp"
_MAX_VALUE_LENGTH = 300


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "service": self.service,
            "event": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            for key, value in fields.items():
                payload[str(key)] = value[:_MAX_VALUE_LENGTH] if isinstance(value, str) else value
        # ensure_ascii escapes every control character, so a value can never break the one-line-per-record format.
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True, default=str)


class _DropAll(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return False


def configure_logging(service: str, stream=None) -> logging.Logger:
    """Install the JSON handler on the `bp` logger (idempotent) and silence Uvicorn's access log."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    logger.addHandler(handler)
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _DropAll) for f in access.filters):
        access.addFilter(_DropAll())
    return logger


def log(level: int, event: str, **fields: object) -> None:
    logging.getLogger(f"{LOGGER_NAME}.app").log(level, event, extra={"fields": fields})
