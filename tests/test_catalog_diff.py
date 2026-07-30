from src.catalog_check.types import Diff, ProviderResult


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
