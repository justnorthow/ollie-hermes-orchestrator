"""Validation rules for the model catalog.

Pure functions, no I/O. The primary correctness check is membership in a
known-model list; the format rules are a cheap typo guard layered on top.

Format rules are deliberately PER-PROVIDER. OpenAI ids legitimately contain
dots (`gpt-5.5`), so a global no-dots rule would reject the one entry that
currently works. We encode only conventions we have verified.
"""
import re

# Applies to every provider: lowercase, starts alphanumeric, no whitespace.
_UNIVERSAL = re.compile(r"^[a-z0-9][a-z0-9.\-]*$")

# Verified conventions only. Anthropic ids are hyphen-separated; a dot is the
# specific defect this catches (`claude-sonnet-4.6` should be `claude-sonnet-4-6`).
_PROVIDER_RULES = {
    "anthropic": (
        re.compile(r"^claude-[a-z0-9]+(-[a-z0-9]+)*$"),
        "anthropic ids are hyphen-separated with no dots "
        "(e.g. claude-sonnet-5, claude-opus-4-8)",
    ),
}

_REQUIRED_FIELDS = ("provider", "id", "label")


def id_format_errors(provider: str, model_id: str) -> list[str]:
    """Return format complaints about `model_id`. Empty list means valid."""
    errors: list[str] = []
    if not _UNIVERSAL.match(model_id or ""):
        errors.append(
            f"{provider}/{model_id!r}: must be lowercase, start with a letter or "
            f"digit, and contain no whitespace"
        )
        return errors  # a junk id fails everything else too; one message is enough

    rule = _PROVIDER_RULES.get(provider)
    if rule is not None:
        pattern, explanation = rule
        if not pattern.match(model_id):
            errors.append(f"{provider}/{model_id!r}: {explanation}")
    return errors


def validate_catalog(models: list[dict], known: dict[str, list[str]]) -> list[str]:
    """Validate catalog entries against required fields, format, and a known list."""
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()

    for entry in models:
        missing = [f for f in _REQUIRED_FIELDS if not entry.get(f)]
        if missing:
            errors.append(f"entry {entry!r} is missing required field(s): {', '.join(missing)}")
            continue

        provider, model_id = entry["provider"], entry["id"]

        key = (provider, model_id)
        if key in seen:
            errors.append(f"{provider}/{model_id}: duplicate catalog entry")
        seen.add(key)

        errors.extend(id_format_errors(provider, model_id))

        known_ids = known.get(provider)
        if known_ids is None:
            errors.append(f"{provider}/{model_id}: no known-model list for provider {provider!r}")
        elif model_id not in known_ids:
            errors.append(
                f"{provider}/{model_id}: not in the known-model list for {provider} "
                f"— typo, or the model was retired"
            )

    return errors
