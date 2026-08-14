"""Logging setup: plain console or structured JSON lines with request ids.

The API and the Celery worker share this setup so one request can be
followed from its ``X-Request-Id`` header into the jobs it queued
(docs/TODO.md §7 "Structured JSON logging with request IDs").
"""

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


def _iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class PlainFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        request_id = request_id_var.get()
        prefix = f" [{request_id}]" if request_id else ""
        message = super().format(record)
        return f"{self.formatTime(record, '%H:%M:%S')} {record.levelname}{prefix} {record.name}: {message}"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": _iso_now(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__
            payload["exc_msg"] = str(record.exc_info[1])
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, default=str)


def setup_logging(log_format: str = "plain", level: int = logging.INFO) -> None:
    """Configure the root logger once; safe to call repeatedly."""
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(PlainFormatter())
    root.addHandler(handler)


def log_extra(**fields) -> dict:
    """Attach structured fields to a log call: ``logger.info("x", extra=log_extra(a=1))``."""
    return {"extra_fields": fields}
