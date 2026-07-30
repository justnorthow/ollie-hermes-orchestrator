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


#: One entry per provider appearing in src.catalog.MODELS.
#: `min_models` is set below the count we expect to find, so it catches a
#: broken scrape without tripping on a single retirement.
SCRAPE_CONFIGS: list[ScrapeConfig] = [
    ScrapeConfig(
        provider="anthropic",
        url="https://platform.claude.com/docs/en/about-claude/models/overview.md",
        pattern=r"claude-[a-z]+-[a-z0-9\-]+",
        min_models=4,
    ),
    ScrapeConfig(
        provider="openai",
        url="https://platform.openai.com/docs/models",
        pattern=r"gpt-[0-9]+(?:\.[0-9]+)*(?:-[a-z]+)*",
        min_models=2,
    ),
    ScrapeConfig(
        provider="groq",
        url="https://console.groq.com/docs/models",
        pattern=r"llama-[0-9]+(?:\.[0-9]+)*-[0-9]+b",
        min_models=1,
    ),
]


def http_fetch(url: str) -> str:
    """Real fetcher. Injected only by __main__ so tests stay offline."""
    response = httpx.get(url, timeout=_TIMEOUT_SECONDS, follow_redirects=True)
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

    if len(found) < config.min_models:
        return ProviderResult(
            config.provider,
            None,
            MECHANISM_NONE,
            f"extracted {len(found)} models, expected at least "
            f"{config.min_models} — page layout may have changed",
        )

    return ProviderResult(config.provider, found, MECHANISM_SCRAPE)


def fetch_all(
    configs: list[ScrapeConfig], fetch: Callable[[str], str]
) -> list[ProviderResult]:
    """Scrape every configured provider. One failure never blocks the others."""
    return [scrape_provider(config, fetch) for config in configs]
