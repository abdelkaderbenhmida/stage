"""Control-plane health metric collectors (docs/TODO.md §4.3).

Redis is absent in CI/dev; the beat pulse and queue depth collectors must
fail soft and report a safe value instead of breaking the /metrics scrape.
"""

import time

import redis
from controlplane.api import metrics
from controlplane.workers import tasks
from prometheus_client import REGISTRY


class _FakeRedis:
    store = {}

    def __init__(self, *a, **kw):
        pass

    @classmethod
    def from_url(cls, *a, **kw):
        return cls()

    def get(self, key):
        return self.store.get(key)

    def llen(self, key):
        return self.store.get(key, 0)

    def set(self, key, value, ex=None):
        assert ex == 90
        self.store[key] = value


def test_health_gauges_registered_and_fail_soft(monkeypatch):
    metrics.register_health_metrics()

    def _no_redis(*args, **kwargs):
        raise ConnectionError("no redis in tests")

    monkeypatch.setattr(redis, "Redis", _no_redis)
    for name in (
        "controlplane_queue_depth",
        "controlplane_beat_seconds_since_pulse",
        "controlplane_jobs_running_over_30_minutes",
        "controlplane_job_failure_ratio_15m",
    ):
        sample = REGISTRY.get_sample_value(name)
        assert sample is not None, f"{name} missing from registry"

    # Queue depth reads 0 when Redis is down; the beat gauge also reads 0
    # (never page about beat liveness because Redis itself is unreachable).
    assert REGISTRY.get_sample_value("controlplane_queue_depth") == 0.0
    assert REGISTRY.get_sample_value("controlplane_beat_seconds_since_pulse") == 0.0


def test_beat_staleness_reports_never_pulsed(settings_override, monkeypatch):
    settings_override(redis_url="redis://fake:6379/0")
    monkeypatch.setattr(redis, "Redis", _FakeRedis)
    metrics.register_health_metrics()
    assert REGISTRY.get_sample_value("controlplane_beat_seconds_since_pulse") > 1e6


def test_beat_pulse_writes_heartbeat(settings_override, monkeypatch):
    settings_override(redis_url="redis://fake:6379/0")
    monkeypatch.setattr(redis, "Redis", _FakeRedis)
    tasks.beat_pulse()
    assert abs(_FakeRedis.store["controlplane:beat:pulse"] - time.time()) < 5