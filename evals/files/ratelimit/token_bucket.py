"""Token bucket rate limiter.

Each key owns a bucket with `capacity` tokens. Tokens refill continuously at
`rate` tokens/second. A request consumes one token; if the bucket is empty the
request is rejected and the caller learns how long to wait.
"""
import time
from dataclasses import dataclass, field


@dataclass
class Bucket:
    tokens: float
    updated_at: float = field(default_factory=time.monotonic)


class TokenBucketLimiter:
    def __init__(self, capacity: int, rate: float):
        self.capacity = capacity
        self.rate = rate
        self._buckets: dict[str, Bucket] = {}

    def _refill(self, b: Bucket, now: float) -> None:
        b.tokens = min(self.capacity, b.tokens + (now - b.updated_at) * self.rate)
        b.updated_at = now

    def allow(self, key: str) -> tuple[bool, float]:
        """Return (allowed, retry_after_seconds)."""
        now = time.monotonic()
        b = self._buckets.setdefault(key, Bucket(tokens=self.capacity, updated_at=now))
        self._refill(b, now)
        if b.tokens >= 1:
            b.tokens -= 1
            return True, 0.0
        return False, (1 - b.tokens) / self.rate
