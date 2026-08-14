"""Rate limiter unit tests (§7.6)."""

from controlplane.api.rate_limit import RateLimiter


def test_allows_within_budget():
    rl = RateLimiter()
    for _ in range(5):
        assert rl.allow("alice", max_events=5, window_seconds=60)


def test_blocks_over_budget():
    rl = RateLimiter()
    for _ in range(5):
        rl.allow("bob", max_events=5, window_seconds=60)
    assert not rl.allow("bob", max_events=5, window_seconds=60)


def test_buckets_are_per_key():
    rl = RateLimiter()
    for _ in range(10):
        rl.allow("carol", max_events=5, window_seconds=60)
    assert rl.allow("dave", max_events=5, window_seconds=60)


def test_window_slides():
    from collections import deque

    rl = RateLimiter()
    rl._events["eve"] = deque([0.0])  # event long in the past
    assert rl.allow("eve", max_events=1, window_seconds=60)
