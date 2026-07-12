"""Structured JSON logging for microservices.

Centralizing log format here means every service emits identical JSON for
log aggregators (Loki, Cloud Logging, ELK). Falls back to a human-readable
format on stdout when no JSON formatter is requested via the env var
LOG_FORMAT=plain (used in local dev).
"""

import logging
import os
import sys
from typing import Optional

from pythonjsonlogger import jsonlogger


_CONFIGURED = False


def setup_logging(service_name: Optional[str] = None, level: Optional[str] = None) -> logging.Logger:
    """Configure root + service logger. Idempotent.

    Args:
        service_name: used as a `service` field in every log record.
        level: logging level name (default: INFO, or LOG_LEVEL env var).

    Returns: the service-named Logger (also writes through the root logger).
    """
    global _CONFIGURED

    log_level = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    log_format = os.environ.get("LOG_FORMAT", "json").lower()

    root = logging.getLogger()
    if not _CONFIGURED:
        root.setLevel(log_level)
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(log_level)
        if log_format == "json":
            formatter = jsonlogger.JsonFormatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                rename_fields={"asctime": "timestamp", "levelname": "level"},
            )
        else:
            formatter = logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S%z",
            )
        handler.setFormatter(formatter)
        root.handlers.clear()
        root.addHandler(handler)
        _CONFIGURED = True
    else:
        root.setLevel(log_level)
        for h in root.handlers:
            h.setLevel(log_level)

    logger_name = service_name or os.environ.get("SERVICE_NAME", "app")
    return logging.getLogger(logger_name)
