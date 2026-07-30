"""Models we know about and deliberately do not offer.

Without this, the weekly catalog check proposes every model a provider serves
but we don't list — permanently. The first live run surfaced 21 such ids, only
four of which were actionable; the rest were superseded versions we had already
ruled out. A report that re-proposes the same dozen models every Monday is a
report nobody reads, which is the failure mode the whole check exists to avoid.

`compute_diff` subtracts these from its `new` category. The reason string is the
point: it is the institutional memory for *why* something isn't on offer, which
otherwise lives only in a commit message.

Two rules for editing this file:

  - Decline only what you are confident about. A model you are merely unsure
    about should keep appearing in the report until a human decides — that is
    the report doing its job.
  - Removing an entry here puts the model back in the report as a candidate.
    That is the intended way to reconsider a decision.
"""

DECLINED: dict[str, dict[str, str]] = {
    "anthropic": {
        # Documented in the freshness spec: not a safe default picker option.
        "claude-fable-5": (
            "Requires 30-day data retention — returns 400 on every request from a "
            "zero-data-retention org. Thinking cannot be disabled, and single turns "
            "on hard tasks can run many minutes."
        ),
        "claude-mythos-5": (
            "Available only through Project Glasswing; not accessible on this account."
        ),
        # Superseded by the Claude 5 line we do offer. Still served by the provider,
        # so they will keep appearing as `new` until declined here.
        "claude-opus-4-8": "Superseded by claude-opus-5.",
        "claude-opus-4-7": "Superseded by claude-opus-5.",
        "claude-opus-4-6": "Superseded by claude-opus-5.",
        "claude-opus-4-5": "Superseded by claude-opus-5.",
        "claude-opus-4-1": "Superseded by claude-opus-5.",
        "claude-sonnet-4-6": "Superseded by claude-sonnet-5.",
        "claude-sonnet-4-5": "Superseded by claude-sonnet-5.",
        # Scrape artifacts, not real model ids — trailing digits fused from adjacent
        # prose on the docs page. Listed so they stop occupying report space. If the
        # extraction pattern is tightened later these simply stop matching.
        "claude-fable-53": "Scrape artifact — not a real model id.",
        "claude-opus-53": "Scrape artifact — not a real model id.",
        "claude-opus-4-76": "Scrape artifact — not a real model id.",
        "claude-opus-4-86": "Scrape artifact — not a real model id.",
        "claude-sonnet-53": "Scrape artifact — not a real model id.",
    },
    "openai": {
        "gpt-4": "Superseded by the GPT-5.6 family.",
        "gpt-5": "Superseded by the GPT-5.6 family.",
        # Confirmed by JB, 2026-07-30, after the check surfaced it as a candidate.
        "gpt-5.6": (
            "Not a model — it is the family heading on the docs page. The selectable "
            "ids are gpt-5.6-sol / gpt-5.6-terra / gpt-5.6-luna, all of which we offer."
        ),
    },
    "groq": {
        "llama-3.1-8b": "Smaller sibling of llama-3.3-70b, which we already offer.",
    },
}


def declined_ids() -> dict[str, list[str]]:
    """Provider -> declined ids, in the shape `compute_diff` consumes."""
    return {provider: sorted(entries) for provider, entries in DECLINED.items()}
