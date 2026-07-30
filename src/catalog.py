# Anthropic ids are hyphen-separated. See tests/test_catalog_rules.py —
# a dotted id (the previous claude-sonnet-4.6 / claude-opus-4.7) 404s at
# the provider because 03-install-profile.sh writes this string verbatim
# into Hermes model.default.
MODELS = [
    {"provider": "anthropic", "id": "claude-opus-5",    "label": "Claude Opus 5"},
    {"provider": "anthropic", "id": "claude-sonnet-5",  "label": "Claude Sonnet 5"},
    {"provider": "anthropic", "id": "claude-haiku-4-5", "label": "Claude Haiku 4.5"},
    {"provider": "openai",    "id": "gpt-5.5",          "label": "GPT-5.5"},
    {"provider": "groq",      "id": "llama-3.3-70b",    "label": "Llama 3.3 70B (Groq)"},
]


def list_models() -> list[dict]:
    return list(MODELS)


def list_skills() -> list[dict]:
    return [
        {"id": "github-repo-management", "label": "GitHub repo management"},
        {"id": "writing-skills", "label": "Writing skills"},
        {"id": "brainstorming", "label": "Brainstorming"},
    ]
