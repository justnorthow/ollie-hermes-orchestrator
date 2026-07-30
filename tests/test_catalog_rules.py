from src.catalog_rules import id_format_errors, validate_catalog


def test_anthropic_hyphenated_id_is_valid():
    assert id_format_errors("anthropic", "claude-sonnet-5") == []
    assert id_format_errors("anthropic", "claude-opus-4-8") == []
    assert id_format_errors("anthropic", "claude-haiku-4-5") == []


def test_anthropic_dotted_id_is_invalid():
    errors = id_format_errors("anthropic", "claude-sonnet-4.6")
    assert errors
    assert "hyphen" in errors[0].lower()


def test_openai_dotted_id_is_valid():
    # gpt-5.5 is a real OpenAI id. A global no-dots rule would be a bug.
    assert id_format_errors("openai", "gpt-5.5") == []
    assert id_format_errors("openai", "gpt-5.6-luna") == []


def test_universal_rules_reject_junk():
    assert id_format_errors("groq", "Llama-3.3-70B")   # uppercase
    assert id_format_errors("groq", "llama 3.3 70b")   # whitespace
    assert id_format_errors("groq", "")                # empty


def test_unknown_provider_gets_universal_rules_only():
    assert id_format_errors("someprovider", "model-1.2") == []
    assert id_format_errors("someprovider", "Model X")


def test_validate_catalog_flags_id_absent_from_known_list():
    models = [{"provider": "openai", "id": "gpt-9.9", "label": "GPT-9.9"}]
    known = {"openai": ["gpt-5.5"]}
    errors = validate_catalog(models, known)
    assert any("gpt-9.9" in e for e in errors)


def test_validate_catalog_flags_duplicate_ids():
    models = [
        {"provider": "openai", "id": "gpt-5.5", "label": "A"},
        {"provider": "openai", "id": "gpt-5.5", "label": "B"},
    ]
    errors = validate_catalog(models, {"openai": ["gpt-5.5"]})
    assert any("duplicate" in e.lower() for e in errors)


def test_validate_catalog_flags_missing_required_field():
    models = [{"provider": "openai", "id": "gpt-5.5"}]  # no label
    errors = validate_catalog(models, {"openai": ["gpt-5.5"]})
    assert any("label" in e for e in errors)


def test_validate_catalog_passes_clean_input():
    models = [{"provider": "openai", "id": "gpt-5.5", "label": "GPT-5.5"}]
    assert validate_catalog(models, {"openai": ["gpt-5.5"]}) == []


import json
from pathlib import Path

KNOWN_MODELS_PATH = Path(__file__).parent / "fixtures" / "known_models.json"


def _load_known() -> dict[str, list[str]]:
    raw = json.loads(KNOWN_MODELS_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def test_known_models_fixture_is_wellformed():
    known = _load_known()
    assert known, "fixture must list at least one provider"
    for provider, ids in known.items():
        assert isinstance(ids, list) and ids, f"{provider} must have a non-empty id list"
        for model_id in ids:
            assert id_format_errors(provider, model_id) == [], (
                f"fixture itself contains a malformed id: {provider}/{model_id}"
            )


def test_live_catalog_is_valid():
    from src.catalog import MODELS

    errors = validate_catalog(MODELS, _load_known())
    assert errors == [], "catalog validation failed:\n" + "\n".join(errors)
