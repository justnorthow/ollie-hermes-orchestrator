# Anthropic ids are hyphen-separated. See tests/test_catalog_rules.py —
# a dotted id (the previous claude-sonnet-4.6 / claude-opus-4.7) 404s at
# the provider because 03-install-profile.sh writes this string verbatim
# into Hermes model.default.
#
# speed_class gates synchronous agent-to-agent consults (see the switchable
# dispatch spec): `fast` peers may be consulted inline, `heavy` peers are
# assign-only. long_context_threshold exists because speed class alone does not
# bound cost — a cheap peer becomes expensive above the threshold.
MODELS = [
    {
        "provider": "anthropic", "id": "claude-opus-5", "label": "Claude Opus 5",
        "speed_class": "heavy", "price_in": 5.00, "price_out": 25.00,
        "long_context_threshold": None, "verified_at": "2026-07-29",
    },
    {
        "provider": "anthropic", "id": "claude-sonnet-5", "label": "Claude Sonnet 5",
        "speed_class": "fast", "price_in": 3.00, "price_out": 15.00,
        "long_context_threshold": None, "verified_at": "2026-07-29",
    },
    {
        "provider": "anthropic", "id": "claude-haiku-4-5", "label": "Claude Haiku 4.5",
        "speed_class": "fast", "price_in": 1.00, "price_out": 5.00,
        "long_context_threshold": None, "verified_at": "2026-07-29",
    },
    # Price left as None deliberately: not verified. Do NOT fill these in from
    # memory or from a search result — read them off the vendor's own pricing
    # page, then set verified_at to that date.
    {
        "provider": "openai", "id": "gpt-5.5", "label": "GPT-5.5",
        "speed_class": "fast", "price_in": None, "price_out": None,
        "long_context_threshold": None, "verified_at": "never",
    },
    {
        "provider": "groq", "id": "llama-3.3-70b", "label": "Llama 3.3 70B (Groq)",
        "speed_class": "fast", "price_in": None, "price_out": None,
        "long_context_threshold": None, "verified_at": "never",
    },
]


def list_models() -> list[dict]:
    return list(MODELS)


def list_skills() -> list[dict]:
    return [
        {"id": "github-repo-management", "label": "GitHub repo management"},
        {"id": "writing-skills", "label": "Writing skills"},
        {"id": "brainstorming", "label": "Brainstorming"},
    ]
