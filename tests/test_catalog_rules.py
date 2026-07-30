import pytest

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
