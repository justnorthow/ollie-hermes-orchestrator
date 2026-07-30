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
    # GPT-5.6 family. Ids confirmed from OpenAI's own docs list via the catalog
    # check's live run on 2026-07-30; prices read off OpenAI's pricing page the
    # same day. gpt-5.5 was retired here because that same run found it absent
    # from the provider's list.
    #
    # long_context_threshold: the 2.0x input / 1.5x output multipliers are
    # vendor-confirmed (Sol $5/$30 -> $10/$45, Terra $2.50/$15 -> $5/$22.50,
    # Luna $1/$6 -> $2/$9). The 272_000-token trigger is NOT stated on OpenAI's
    # pricing page and comes from a third-party aggregator — re-check it before
    # relying on the exact boundary.
    {
        "provider": "openai", "id": "gpt-5.6-sol", "label": "GPT-5.6 Sol",
        "speed_class": "heavy", "price_in": 5.00, "price_out": 30.00,
        "long_context_threshold": {
            "tokens": 272_000, "input_multiplier": 2.0, "output_multiplier": 1.5,
        },
        "verified_at": "2026-07-30",
    },
    {
        "provider": "openai", "id": "gpt-5.6-terra", "label": "GPT-5.6 Terra",
        "speed_class": "fast", "price_in": 2.50, "price_out": 15.00,
        "long_context_threshold": {
            "tokens": 272_000, "input_multiplier": 2.0, "output_multiplier": 1.5,
        },
        "verified_at": "2026-07-30",
    },
    {
        "provider": "openai", "id": "gpt-5.6-luna", "label": "GPT-5.6 Luna",
        "speed_class": "fast", "price_in": 1.00, "price_out": 6.00,
        "long_context_threshold": {
            "tokens": 272_000, "input_multiplier": 2.0, "output_multiplier": 1.5,
        },
        "verified_at": "2026-07-30",
    },
    # Price still unverified — do NOT fill this in from memory or a search
    # result; read it off the vendor's own pricing page, then set verified_at.
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
