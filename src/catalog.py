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
    # same day.
    #
    # WHAT THIS LIST IS NOT: the boxes reach OpenAI through Hermes's
    # `openai-codex` provider (chatgpt.com/backend-api/codex), which on
    # 2026-07-30 served ten ids — the three below plus gpt-5.5, gpt-5.4,
    # gpt-5.4-mini, gpt-5.3-codex-spark, and -pro variants of all three 5.6
    # models. We deliberately offer a subset; the others are available and
    # simply not on the picker. Do NOT treat their absence here as evidence
    # they are retired, and do not delete an id from this file because the
    # weekly check could not find it on platform.openai.com/docs — that page
    # does not describe the Codex route. See catalog_check/providers.py.
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
    # Still served by openai-codex and still what tests/conftest.py seeds as a
    # profile's model.default. Availability and speed class confirmed by JB
    # against a live profile's model picker on 2026-07-30, after the weekly
    # check wrongly reported it retired and it was briefly deleted from here.
    #
    # heavy => listed as a teammate but NOT consult-eligible, so no agent can
    # block its own turn waiting on it.
    #
    # verified_at stays "never" on purpose even though JB confirmed the id and
    # the speed class today: in this schema verified_at gates the PRICE (see
    # tests/test_catalog_rules.py), and the price is genuinely unknown. These
    # models are reached over OAuth through Codex rather than the metered
    # first-party API, so the docs' per-token rates may not describe what this
    # costs at all. Leaving it "never" keeps the entry in the weekly report's
    # stale list until someone establishes the real number. Do not fill it in
    # from memory to silence that.
    {
        "provider": "openai", "id": "gpt-5.5", "label": "GPT-5.5",
        "speed_class": "heavy", "price_in": None, "price_out": None,
        "long_context_threshold": None, "verified_at": "never",
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
