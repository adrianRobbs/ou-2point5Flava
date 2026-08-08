import time

from ou25_pipeline.api.client import RateLimiter


def test_allows_burst_up_to_capacity():
    limiter = RateLimiter(requests_per_minute=60)
    start = time.monotonic()
    for _ in range(5):
        limiter.acquire()
    # First `capacity` acquires should be near-instant (bucket starts full).
    assert time.monotonic() - start < 0.2


def test_blocks_once_capacity_exhausted():
    # Bucket capacity == requests_per_minute (bursting up to the full plan
    # quota is allowed) — drain it, then the next acquire must wait for a
    # refill: 120/min == 2/sec, so ~0.5s for one token.
    limiter = RateLimiter(requests_per_minute=120)
    for _ in range(120):
        limiter.acquire()
    start = time.monotonic()
    limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed > 0.3


def test_refills_over_time():
    limiter = RateLimiter(requests_per_minute=600)  # 10/sec
    for _ in range(10):
        limiter.acquire()
    time.sleep(0.2)  # ~2 tokens should refill
    start = time.monotonic()
    limiter.acquire()
    limiter.acquire()
    # Two refilled tokens should be available without much additional wait.
    assert time.monotonic() - start < 0.15
