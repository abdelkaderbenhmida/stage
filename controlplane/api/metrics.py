"""Control-plane health metrics for AlertManager (docs/OPERATIONS.md §5).

The FastAPI instrumentator already exports request latency / error-rate.
What the spec's alert list additionally needs is knowledge the instrumentator
cannot see: Celery queue depth (workers dead?), beat liveness (node health
polling stopped?), and job outcomes (failure rate, hung runs). All of those
live in Redis or the jobs table, so they are exposed here as gauges that are
recomputed on every scrape — no worker-side exporter or multiprocess
registry required. Every collector fails soft: a missing Redis or database
reports 0/never-raised rather than failing the /metrics scrape.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import redis
import sqlalchemy as sa
from prometheus_client import Gauge

from controlplane.core.config import settings
from controlplane.db import engine

_QUEUE_KEY = "celery"  # default Celery broker queue
_BEAT_KEY = "controlplane:beat:pulse"

_QUEUE_DEPTH = Gauge(
    "controlplane_queue_depth",
    "Jobs waiting on the Celery broker queue. Rising steadily means workers are dead or wedged.",
)
_BEAT_STALENESS = Gauge(
    "controlplane_beat_seconds_since_pulse",
    "Seconds since the Celery beat process last wrote its heartbeat. Growing means "
    "node-health polling (poll_nodes) has stopped.",
)
_RUNNING_OVER_30M = Gauge(
    "controlplane_jobs_running_over_30_minutes",
    "Jobs in status=running for more than 30 minutes. Usually a hung terraform/ansible run holding a project lock.",
)
_FAILURE_RATIO = Gauge(
    "controlplane_job_failure_ratio_15m",
    "Share of jobs finished in the last 15 minutes that failed (0..1).",
)


def _redis() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)


def _queue_depth() -> float:
    depth = _redis().llen(_QUEUE_KEY)
    return float(depth or 0)


def _beat_staleness() -> float:
    raw = _redis().get(_BEAT_KEY)
    if not raw:
        return float(10**9)  # never pulsed — raise immediately
    return max(0.0, datetime.now(UTC).timestamp() - float(raw))


def _running_over_30m() -> float:
    cutoff = datetime.now(UTC) - timedelta(minutes=30)
    with engine.connect() as conn:
        count = conn.execute(
            sa.text("SELECT count(*) FROM jobs WHERE status = 'running' AND started_at < :cutoff"),
            {"cutoff": cutoff},
        ).scalar()
    return float(count or 0)


def _failure_ratio() -> float:
    window = datetime.now(UTC) - timedelta(minutes=15)
    with engine.connect() as conn:
        failed = conn.execute(
            sa.text("SELECT count(*) FROM jobs WHERE status = 'failed' AND finished_at >= :window"),
            {"window": window},
        ).scalar_one()
        total = conn.execute(
            sa.text(
                "SELECT count(*) FROM jobs WHERE finished_at >= :window "
                "AND status IN ('failed', 'succeeded')"
            ),
            {"window": window},
        ).scalar_one()
    return float(failed / total) if total else 0.0


def register_health_metrics() -> None:
    """Call once at app startup. Failures inside collectors never propagate."""
    _QUEUE_DEPTH.set_function(_safe(_queue_depth))
    _BEAT_STALENESS.set_function(_safe(_beat_staleness))
    _RUNNING_OVER_30M.set_function(_safe(_running_over_30m))
    _FAILURE_RATIO.set_function(_safe(_failure_ratio))


def _safe(fn):
    def _guarded():
        try:
            return fn()
        except Exception:
            return 0.0

    return _guarded
