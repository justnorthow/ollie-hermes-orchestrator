from datetime import date

from src.catalog_check.diff import compute_diff
from src.catalog_check.types import Diff, ProviderResult


TODAY = date(2026, 7, 29)


def _catalog(*entries):
    return [
        {"provider": p, "id": i, "label": i, **extra}
        for p, i, extra in entries
    ]


def test_provider_result_none_ids_means_unavailable():
    r = ProviderResult(provider="openai", model_ids=None, mechanism="none", detail="no key")
    assert r.model_ids is None
    assert r.detail == "no key"


def test_diff_blocking_only_on_unknown():
    clean = Diff(unknown=[], new=[("openai", "gpt-9")], unverifiable=[], stale=[])
    assert clean.has_blocking_findings is False

    broken = Diff(unknown=[("anthropic", "claude-x")], new=[], unverifiable=[], stale=[])
    assert broken.has_blocking_findings is True


def test_diff_unverifiable_alone_is_not_blocking():
    d = Diff(
        unknown=[],
        new=[],
        unverifiable=[ProviderResult("groq", None, "none", "unreachable")],
        stale=[],
    )
    assert d.has_blocking_findings is False


def test_catalog_id_absent_from_provider_is_unknown():
    models = _catalog(("openai", "gpt-old", {}))
    results = [ProviderResult("openai", frozenset({"gpt-5.5"}), "scrape")]

    d = compute_diff(models, results, TODAY)

    assert d.unknown == [("openai", "gpt-old")]
    assert d.has_blocking_findings is True


def test_provider_model_absent_from_catalog_is_new():
    models = _catalog(("openai", "gpt-5.5", {}))
    results = [ProviderResult("openai", frozenset({"gpt-5.5", "gpt-5.6-luna"}), "scrape")]

    d = compute_diff(models, results, TODAY)

    assert d.new == [("openai", "gpt-5.6-luna")]
    assert d.unknown == []
    assert d.has_blocking_findings is False


def test_unavailable_provider_produces_unverifiable_not_unknown():
    # The critical case: a provider we could not reach must NOT mark its
    # catalog ids as retired.
    models = _catalog(("groq", "llama-3.3-70b", {}))
    results = [ProviderResult("groq", None, "none", "no scraper configured")]

    d = compute_diff(models, results, TODAY)

    assert d.unknown == []
    assert d.new == []
    assert [r.provider for r in d.unverifiable] == ["groq"]
    assert d.has_blocking_findings is False


def test_provider_in_catalog_with_no_result_at_all_is_unverifiable():
    # A provider silently dropped from the fetch list must still be reported.
    models = _catalog(("groq", "llama-3.3-70b", {}))

    d = compute_diff(models, [], TODAY)

    assert [r.provider for r in d.unverifiable] == ["groq"]
    assert d.unknown == []


def test_stale_verified_at_is_flagged():
    models = _catalog(("openai", "gpt-5.5", {"verified_at": "2025-01-01"}))
    results = [ProviderResult("openai", frozenset({"gpt-5.5"}), "scrape")]

    d = compute_diff(models, results, TODAY, stale_after_days=365)

    assert d.stale == [("openai", "gpt-5.5", "2025-01-01")]
    assert d.has_blocking_findings is False


def test_recent_verified_at_is_not_flagged():
    models = _catalog(("openai", "gpt-5.5", {"verified_at": "2026-07-01"}))
    results = [ProviderResult("openai", frozenset({"gpt-5.5"}), "scrape")]

    d = compute_diff(models, results, TODAY, stale_after_days=365)

    assert d.stale == []


def test_missing_verified_at_is_flagged_as_never():
    models = _catalog(("openai", "gpt-5.5", {}))
    results = [ProviderResult("openai", frozenset({"gpt-5.5"}), "scrape")]

    d = compute_diff(models, results, TODAY)

    assert d.stale == [("openai", "gpt-5.5", "never")]


def test_unparseable_verified_at_is_flagged_as_never():
    models = _catalog(("openai", "gpt-5.5", {"verified_at": "last Tuesday"}))
    results = [ProviderResult("openai", frozenset({"gpt-5.5"}), "scrape")]

    d = compute_diff(models, results, TODAY)

    assert d.stale == [("openai", "gpt-5.5", "never")]


def test_clean_catalog_produces_empty_diff():
    models = _catalog(("openai", "gpt-5.5", {"verified_at": "2026-07-01"}))
    results = [ProviderResult("openai", frozenset({"gpt-5.5"}), "scrape")]

    d = compute_diff(models, results, TODAY)

    assert d.is_empty is True


def test_provider_serving_no_models_is_available_and_retires_its_catalog_ids():
    """An empty frozenset is a real answer: the provider serves nothing, so its
    catalog ids ARE retired. This is the opposite of model_ids=None, which means
    we could not check. Conflating the two is the failure mode the design guards."""
    models = _catalog(("openai", "gpt-5.5", {"verified_at": "2026-07-01"}))
    results = [ProviderResult("openai", frozenset(), "scrape")]

    d = compute_diff(models, results, TODAY)

    assert results[0].available is True
    assert d.unknown == [("openai", "gpt-5.5")]
    assert d.unverifiable == []


def test_duplicate_provider_results_do_not_produce_contradictory_diff():
    """If results contain two entries for the same provider (e.g., a failed and
    successful fetch), the diff must be self-consistent: aggregation must deduplicate
    consistently. The provider should not appear in both unverifiable and with a
    clean unknown list."""
    models = _catalog(("openai", "gpt-5.5", {"verified_at": "2026-07-01"}))
    # Two entries for openai: one failed (None models), one succeeded (with gpt-5.5).
    # Last-wins deduplication keeps the successful one.
    results = [
        ProviderResult("openai", None, "none", "first attempt failed"),
        ProviderResult("openai", frozenset({"gpt-5.5"}), "scrape"),
    ]

    d = compute_diff(models, results, TODAY)

    # Provider should not appear in unverifiable (last-wins keeps the successful result)
    assert [r.provider for r in d.unverifiable] == []
    # Catalog id should not be marked unknown (it's in the successful result)
    assert d.unknown == []
    # Overall result should be clean
    assert d.is_empty is True


def test_declined_ids_are_excluded_from_new():
    models = _catalog(("openai", "gpt-5.6-terra", {"verified_at": "2026-07-01"}))
    results = [ProviderResult("openai", frozenset({"gpt-5.6-terra", "gpt-4"}), "scrape")]

    d = compute_diff(models, results, TODAY, declined={"openai": ["gpt-4"]})

    assert d.new == []


def test_declined_does_not_suppress_unknown():
    """A declined id sitting in the catalog is still a finding — being on offer
    in the picker is what `unknown` is about, regardless of our intent."""
    models = _catalog(("openai", "gpt-4", {"verified_at": "2026-07-01"}))
    results = [ProviderResult("openai", frozenset({"gpt-5.6-terra"}), "scrape")]

    d = compute_diff(models, results, TODAY, declined={"openai": ["gpt-4"]})

    assert d.unknown == [("openai", "gpt-4")]


def test_declined_omitted_keeps_previous_behaviour():
    models = _catalog(("openai", "gpt-5.6-terra", {"verified_at": "2026-07-01"}))
    results = [ProviderResult("openai", frozenset({"gpt-5.6-terra", "gpt-4"}), "scrape")]

    assert compute_diff(models, results, TODAY).new == [("openai", "gpt-4")]


def test_declined_is_scoped_per_provider():
    models = _catalog(("openai", "gpt-5.6-terra", {"verified_at": "2026-07-01"}))
    results = [ProviderResult("openai", frozenset({"gpt-5.6-terra", "gpt-4"}), "scrape")]

    # "gpt-4" declined under a different provider must not filter openai's.
    d = compute_diff(models, results, TODAY, declined={"anthropic": ["gpt-4"]})

    assert d.new == [("openai", "gpt-4")]


def test_real_declined_list_is_wellformed_and_does_not_contradict_the_catalog():
    from src.catalog import MODELS
    from src.catalog_declined import DECLINED, declined_ids

    catalogued = {(m["provider"], m["id"]) for m in MODELS}
    for provider, entries in DECLINED.items():
        for model_id, reason in entries.items():
            assert reason.strip(), f"{provider}/{model_id} has no reason"
            assert (provider, model_id) not in catalogued, (
                f"{provider}/{model_id} is both declined and on offer in MODELS"
            )

    shaped = declined_ids()
    assert set(shaped) == set(DECLINED)
    assert all(isinstance(v, list) for v in shaped.values())


# --- absence is not symmetric with presence -----------------------------------
#
# The regression these pin: on 2026-07-30 the openai scrape found gpt-5.5 absent
# from platform.openai.com/docs/models, `unknown` turned CI red, and the model
# was deleted from the catalog — while Hermes's openai-codex provider was still
# serving it, and tests/conftest.py still seeded it as a profile's model.default.
# Finding an id on a docs page proves the model exists; not finding one proves
# only that the page does not document it.

def test_absence_from_a_partial_source_is_review_not_blocking():
    """A source that covers one route into a provider cannot testify that a
    model reached over another route is gone."""
    diff = compute_diff(
        _catalog(("openai", "gpt-5.5", {"verified_at": "never"})),
        [ProviderResult("openai", frozenset({"gpt-5.6-sol"}), "scrape",
                        absence_is_authoritative=False)],
        TODAY,
    )

    assert diff.unlisted == [("openai", "gpt-5.5")]
    assert diff.unknown == []
    assert diff.has_blocking_findings is False


def test_absence_from_a_complete_source_still_blocks():
    """The relaxation must not leak to providers whose source really does
    enumerate everything — otherwise a genuine typo stops failing the run."""
    diff = compute_diff(
        _catalog(("anthropic", "claude-nonesuch-9", {"verified_at": "never"})),
        [ProviderResult("anthropic", frozenset({"claude-opus-5"}), "scrape")],
        TODAY,
    )

    assert diff.unknown == [("anthropic", "claude-nonesuch-9")]
    assert diff.unlisted == []
    assert diff.has_blocking_findings is True


def test_authoritative_absence_is_the_default():
    """A ScrapeConfig author who says nothing gets the strict behaviour. Opting
    out has to be a deliberate, documented act."""
    assert ProviderResult("x", frozenset(), "scrape").absence_is_authoritative is True


def test_unlisted_alone_is_not_a_clean_run():
    """It must still appear in the report — advisory, not invisible."""
    diff = Diff(unlisted=[("openai", "gpt-5.5")])
    assert diff.is_empty is False
    assert diff.has_blocking_findings is False
