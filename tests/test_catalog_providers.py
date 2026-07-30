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


# --- reject filter -----------------------------------------------------------


def test_reject_drops_matches_after_min_models_are_still_extracted():
    config = ScrapeConfig(
        provider="anthropic",
        url="https://example.test/models",
        pattern=r"claude-[a-z0-9\-]+",
        min_models=1,
        reject=r"bogus",
    )
    page = "claude-opus-5 and claude-bogus-junk"

    result = scrape_provider(config, fetch=lambda url: page)

    assert result.available is True
    assert "claude-opus-5" in result.model_ids
    assert not any("bogus" in m for m in result.model_ids)


def test_reject_below_min_models_after_filtering_is_unavailable():
    # A page consisting only of rejected noise must still trip the guard —
    # the reject filter must not be able to fake a clean scrape.
    config = ScrapeConfig(
        provider="anthropic",
        url="https://example.test/models",
        pattern=r"claude-[a-z0-9\-]+",
        min_models=1,
        reject=r"bogus",
    )
    page = "claude-bogus-one claude-bogus-two"

    result = scrape_provider(config, fetch=lambda url: page)

    assert result.available is False


# --- the real Anthropic pattern + reject, from providers.SCRAPE_CONFIGS -----


def _anthropic_config(min_models=4):
    import dataclasses

    from src.catalog_check.providers import SCRAPE_CONFIGS

    real = next(c for c in SCRAPE_CONFIGS if c.provider == "anthropic")
    # Pattern-behavior tests below use short test pages that legitimately
    # contain fewer ids than the real min_models guard expects — that guard
    # is exercised separately in test_scrape_below_min_models_is_unavailable
    # and the module-level tests using CONFIG. Here we isolate the pattern
    # + reject behavior from the min_models threshold.
    return dataclasses.replace(real, min_models=min_models)


def test_anthropic_pattern_matches_real_catalog_ids():
    config = _anthropic_config(min_models=1)
    page = "Models: claude-opus-5, claude-sonnet-5, claude-haiku-4-5 are live."

    result = scrape_provider(config, fetch=lambda url: page)

    assert result.available is True
    assert result.model_ids == frozenset(
        {"claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"}
    )


def test_anthropic_pattern_is_family_agnostic_for_a_brand_new_name():
    # Must not hardcode known family names — that would defeat the point of
    # a discovery check. A never-seen-before family with the same shape
    # (name + numeric version) must still be found.
    config = _anthropic_config(min_models=1)
    page = "Introducing claude-nova-6 today."

    result = scrape_provider(config, fetch=lambda url: page)

    assert result.available is True
    assert "claude-nova-6" in result.model_ids


def test_anthropic_pattern_and_reject_drop_connective_prose():
    config = _anthropic_config(min_models=1)
    page = (
        "claude-opus-5, claude-sonnet-5, claude-haiku-4-5, claude-fable-5-and-"
        "claude-mythos-5 are available claude-in-amazon-bedrock and "
        "claude-in-microsoft-foundry."
    )

    result = scrape_provider(config, fetch=lambda url: page)

    assert result.available is True
    assert "claude-in-amazon-bedrock" not in result.model_ids
    assert "claude-in-microsoft-foundry" not in result.model_ids
    # The fused "...-and-..." run must split into two clean ids, not one
    # garbage one.
    assert "claude-fable-5" in result.model_ids
    assert "claude-mythos-5" in result.model_ids


def test_anthropic_pattern_and_reject_drop_dated_snapshot_suffix():
    # "claude-opus-5" followed directly by an 8-digit date is exactly the
    # case the pattern's own second optional numeric group can absorb
    # whole (it does not know a date from a minor-version number) — this is
    # what the reject filter's `\d{8}` rule exists to catch.
    config = _anthropic_config(min_models=1)
    page = (
        "claude-opus-5, claude-sonnet-5, claude-haiku-4-5, and the pinned "
        "snapshot claude-opus-5-20250219 for API stability."
    )

    result = scrape_provider(config, fetch=lambda url: page)

    assert result.available is True
    assert "claude-opus-5-20250219" not in result.model_ids
    assert "claude-opus-5" in result.model_ids
