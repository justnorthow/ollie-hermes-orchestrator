from src.rate_limit import TokenBucket


def test_bucket_allows_initial_burst():
    b = TokenBucket(rate_per_min=10)
    for _ in range(10):
        assert b.take("1.2.3.4")
    assert not b.take("1.2.3.4")


def test_bucket_is_per_key():
    b = TokenBucket(rate_per_min=2)
    assert b.take("a")
    assert b.take("a")
    assert not b.take("a")
    assert b.take("b")


def test_idle_buckets_are_evicted(monkeypatch):
    """On an internet-exposed port a per-IP bucket dict grows forever. Buckets
    that have fully refilled are identical to fresh ones, so they must be swept
    rather than retained per unique source IP."""
    import src.rate_limit as rl
    clock = {"t": 1000.0}
    monkeypatch.setattr(rl.time, "monotonic", lambda: clock["t"])

    b = rl.TokenBucket(rate_per_min=60, sweep_interval_s=10.0)  # 1 token/sec, cap 60
    for i in range(100):
        b.take(f"ip-{i}")
    assert len(b._buckets) == 100

    clock["t"] += 200.0          # long enough for every idle bucket to fully refill
    b.take("trigger-sweep")      # triggers the sweep
    assert len(b._buckets) <= 1  # idle clients evicted; only the just-active key may remain
