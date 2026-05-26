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
