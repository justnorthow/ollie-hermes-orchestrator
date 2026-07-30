"""Unauthenticated docs scraping for provider model lists.

Scraping is the PRIMARY mechanism, not a fallback. OAuth access tokens are
short-lived and refresh interactively, so an unattended weekly CI job cannot
hold provider credentials; an unauthenticated page fetch works indefinitely.

The minimum-model guard is the important part. A vendor layout change that
breaks extraction would otherwise return a near-empty set and mark the whole
catalog retired. Below `min_models` we report unavailable instead.
"""
from dataclasses import dataclass
import re
from typing import Callable

import httpx

from src.catalog_check.types import (
    MECHANISM_NONE,
    MECHANISM_SCRAPE,
    ProviderResult,
)

_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class ScrapeConfig:
    provider: str
    url: str
    pattern: str
    min_models: int
    #: Optional regex; any extracted id matching it (via re.search) is
    #: dropped before the min_models guard runs. Used to strip connective
    #: prose ("claude-in-amazon-bedrock"), dated snapshot suffixes, and
    #: versioned aliases that the extraction pattern cannot avoid matching
    #: on its own without hardcoding known model-family names.
    reject: str | None = None
    #: Does this source enumerate EVERY id we could be served, so that an id's
    #: absence from it means the id is genuinely gone?
    #:
    #: Presence and absence are not symmetric evidence. Finding an id on a docs
    #: page proves the model exists; not finding one proves only that the page
    #: does not document it. Set this False whenever the boxes reach a provider
    #: through a route the scraped page does not describe — then a catalog id
    #: missing from the scrape is reported for a human to look at rather than
    #: failing the run.
    #:
    #: This is not hypothetical. On 2026-07-30 the openai scrape found gpt-5.5
    #: absent from platform.openai.com/docs/models, the run went red, and the
    #: model was deleted from the catalog — while Hermes's openai-codex
    #: provider was still serving it, and still serving five more ids that
    #: page has never listed. tests/conftest.py seeds gpt-5.5 as the default
    #: profile model, so the check had talked us out of offering the model the
    #: boxes actually boot with.
    absence_is_authoritative: bool = True


#: One entry per provider appearing in src.catalog.MODELS.
#: `min_models` is set below the count we expect to find, so it catches a
#: broken scrape without tripping on a single retirement.
SCRAPE_CONFIGS: list[ScrapeConfig] = [
    ScrapeConfig(
        provider="anthropic",
        url="https://platform.claude.com/docs/en/about-claude/models/overview.md",
        # Anchored on a numeric version tail so prose like "claude-in-amazon-
        # bedrock" (no digits) never matches at all, and word-bounded so a
        # run like "...-and-claude-mythos-5" splits into two clean ids
        # instead of one fused one. Deliberately family-agnostic — no known
        # family name is hardcoded — so a brand-new "claude-<newname>-N"
        # is still discovered.
        pattern=r"\bclaude-[a-z]+(?:-[a-z]+)?-[0-9]+(?:-[0-9]+)?\b",
        min_models=4,
        # Belt-and-suspenders for the pattern above: strips connective
        # prose ("-in-", "-and-", ...), 8-digit dated snapshots (e.g. a
        # trailing "-20250219" that the pattern's own numeric tail can
        # accidentally absorb), and "-vN" aliasing suffixes.
        reject=r"-(?:in|and|on|for|with|or)-|\d{8}|-v[0-9]+$",
    ),
    ScrapeConfig(
        provider="openai",
        url="https://platform.openai.com/docs/models",
        pattern=r"gpt-[0-9]+(?:\.[0-9]+)*(?:-[a-z]+)*",
        min_models=2,
        # The boxes do not reach OpenAI through the first-party API this page
        # documents. Hermes's `openai-codex` provider talks to
        # chatgpt.com/backend-api/codex, and on 2026-07-30 it offered ten ids
        # where this page accounted for three of them: gpt-5.5, gpt-5.4,
        # gpt-5.4-mini, gpt-5.3-codex-spark and -pro variants of the whole 5.6
        # family are all served and none are documented here.
        #
        # So this page is a good source for "a new model shipped" and a bad one
        # for "a model went away". Checking the authoritative surface would mean
        # authenticating to the Codex backend, which an unattended weekly job
        # cannot do — the OAuth token refreshes interactively. Until that
        # changes, a missing openai id is a question for a human, not a failure.
        absence_is_authoritative=False,
    ),
    ScrapeConfig(
        provider="groq",
        url="https://console.groq.com/docs/models",
        pattern=r"llama-[0-9]+(?:\.[0-9]+)*-[0-9]+b",
        min_models=1,
    ),
]


def http_fetch(url: str, transport: httpx.BaseTransport | None = None) -> str:
    """Real fetcher. Injected only by __main__ so tests stay offline.

    `transport` is normally left as None (the real network); tests pass an
    `httpx.MockTransport` to exercise this function's contract (timeout,
    raise-on-error) without touching the network.
    """
    with httpx.Client(transport=transport) as client:
        response = client.get(url, timeout=_TIMEOUT_SECONDS, follow_redirects=True)
        response.raise_for_status()
        return response.text


def scrape_provider(
    config: ScrapeConfig, fetch: Callable[[str], str]
) -> ProviderResult:
    """Fetch and extract model ids. Never raises."""
    try:
        body = fetch(config.url)
    except Exception as exc:  # noqa: BLE001 — any failure is "unverifiable"
        return ProviderResult(
            config.provider, None, MECHANISM_NONE, f"fetch failed: {exc}"
        )

    found = frozenset(m.group(0) for m in re.finditer(config.pattern, body))

    if config.reject:
        reject_re = re.compile(config.reject)
        found = frozenset(m for m in found if not reject_re.search(m))

    if len(found) < config.min_models:
        return ProviderResult(
            config.provider,
            None,
            MECHANISM_NONE,
            f"extracted {len(found)} models, expected at least "
            f"{config.min_models} — page layout may have changed",
        )

    return ProviderResult(
        config.provider,
        found,
        MECHANISM_SCRAPE,
        absence_is_authoritative=config.absence_is_authoritative,
    )


def fetch_all(
    configs: list[ScrapeConfig], fetch: Callable[[str], str]
) -> list[ProviderResult]:
    """Scrape every configured provider. One failure never blocks the others."""
    return [scrape_provider(config, fetch) for config in configs]
