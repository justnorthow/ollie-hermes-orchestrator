import time
from fastapi import HTTPException, Request, status


class _Bucket:
    __slots__ = ("tokens", "updated")
    def __init__(self, tokens: float, updated: float) -> None:
        self.tokens = tokens
        self.updated = updated


class TokenBucket:
    """Simple in-memory token bucket. `rate_per_min` ops per minute per key.

    Buckets that have fully refilled are periodically swept, so the per-key map
    can't grow without bound on an internet-exposed port (one entry per unique
    source IP would otherwise leak memory forever)."""

    def __init__(self, rate_per_min: int = 10, sweep_interval_s: float = 300.0) -> None:
        self.rate = rate_per_min / 60.0
        self.capacity = float(rate_per_min)
        self._buckets: dict[str, _Bucket] = {}
        self._sweep_interval = sweep_interval_s
        self._last_sweep = time.monotonic()

    def _tokens_at(self, b: "_Bucket", now: float) -> float:
        return min(self.capacity, b.tokens + (now - b.updated) * self.rate)

    def _maybe_sweep(self, now: float) -> None:
        if now - self._last_sweep < self._sweep_interval:
            return
        self._last_sweep = now
        # A fully-refilled bucket is indistinguishable from a fresh one, so
        # dropping it is safe and reclaims the memory.
        stale = [k for k, b in self._buckets.items() if self._tokens_at(b, now) >= self.capacity]
        for k in stale:
            del self._buckets[k]

    def take(self, key: str) -> bool:
        now = time.monotonic()
        self._maybe_sweep(now)
        b = self._buckets.get(key)
        if b is None:
            b = _Bucket(self.capacity, now)
            self._buckets[key] = b
        b.tokens = self._tokens_at(b, now)
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
