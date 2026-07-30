import pytest

from src.catalog_check.providers import ScrapeConfig, fetch_all, scrape_provider

CONFIG = ScrapeConfig(
    provider="anthropic",
    url="https://example.test/models",
    pattern=r"claude-[a-z0-9\-]+",
    min_models=2,
)


def test_scrape_extracts_ids():
    page = "Models: claude-opus-5 and claude-sonnet-5 are available."

    result = scrape_provider(CONFIG, fetch=lambda url: page)

    assert result.available is True
    assert result.model_ids == frozenset({"claude-opus-5", "claude-sonnet-5"})
    assert result.mechanism == "scrape"


def test_scrape_below_min_models_is_unavailable():
    # A layout change that breaks extraction must not look like mass retirement.
    page = "Models: claude-opus-5 only."

    result = scrape_provider(CONFIG, fetch=lambda url: page)

    assert result.available is False
    assert "1" in result.detail and "2" in result.detail


def test_scrape_finding_nothing_is_unavailable():
    result = scrape_provider(CONFIG, fetch=lambda url: "<html>nope</html>")

    assert result.available is False


def test_scrape_fetch_exception_is_unavailable_not_raised():
    def boom(url):
        raise RuntimeError("connection reset")

    result = scrape_provider(CONFIG, fetch=boom)

    assert result.available is False
    assert "connection reset" in result.detail


def test_fetch_all_isolates_one_failing_provider():
    good = ScrapeConfig("groq", "https://example.test/g", r"llama-[a-z0-9.\-]+", 1)
    bad = ScrapeConfig("openai", "https://example.test/o", r"gpt-[a-z0-9.\-]+", 1)

    def fetch(url):
        if url.endswith("/o"):
            raise RuntimeError("403")
        return "llama-3.3-70b"

    results = fetch_all([good, bad], fetch=fetch)

    by_provider = {r.provider: r for r in results}
    assert by_provider["groq"].available is True
    assert by_provider["openai"].available is False
    assert len(results) == 2


def test_real_configs_cover_every_catalog_provider():
    from src.catalog import MODELS
    from src.catalog_check.providers import SCRAPE_CONFIGS

    configured = {c.provider for c in SCRAPE_CONFIGS}
    catalogued = {m["provider"] for m in MODELS}
    assert catalogued <= configured, (
        f"no scraper configured for: {sorted(catalogued - configured)}"
    )
