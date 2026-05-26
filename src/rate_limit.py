import time
from collections import defaultdict
from fastapi import HTTPException, Request, status


class _Bucket:
    __slots__ = ("tokens", "updated")
    def __init__(self, tokens: float, updated: float) -> None:
        self.tokens = tokens
        self.updated = updated


class TokenBucket:
    """Simple in-memory token bucket. `rate_per_min` ops per minute per key."""

    def __init__(self, rate_per_min: int = 10) -> None:
        self.rate = rate_per_min / 60.0
        self.capacity = float(rate_per_min)
        self._buckets: dict[str, _Bucket] = defaultdict(
            lambda: _Bucket(self.capacity, time.monotonic())
        )

    def take(self, key: str) -> bool:
        now = time.monotonic()
        b = self._buckets[key]
        elapsed = now - b.updated
        b.tokens = min(self.capacity, b.tokens + elapsed * self.rate)
        b.updated = now
        if b.tokens < 1:
            return False
        b.tokens -= 1
        return True


def rate_limit_dep(bucket: TokenBucket):
    async def _dep(request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        if not bucket.take(client_ip):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="rate limit exceeded",
            )
    return _dep
