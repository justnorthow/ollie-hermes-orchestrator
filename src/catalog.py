MODELS = [
    {"provider": "anthropic", "id": "claude-sonnet-4.6", "label": "Claude Sonnet 4.6"},
    {"provider": "anthropic", "id": "claude-opus-4.7",   "label": "Claude Opus 4.7"},
    {"provider": "openai",    "id": "gpt-5.5",            "label": "GPT-5.5"},
    {"provider": "groq",      "id": "llama-3.3-70b",      "label": "Llama 3.3 70B (Groq)"},
]


def list_models() -> list[dict]:
    return list(MODELS)


def list_skills() -> list[dict]:
    return [
        {"id": "github-repo-management", "label": "GitHub repo management"},
        {"id": "writing-skills", "label": "Writing skills"},
        {"id": "brainstorming", "label": "Brainstorming"},
    ]
