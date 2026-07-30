"""Shared result vocabulary for the catalog freshness check.

`ProviderResult.model_ids is None` is the load-bearing distinction: it means
"we could not check this provider", which is reported as unverifiable rather
than being treated as an empty model list. An empty frozenset would wrongly
mark every catalog id as retired.
"""
from dataclasses import dataclass, field

MECHANISM_SCRAPE = "scrape"
MECHANISM_API = "api"
MECHANISM_NONE = "none"


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    model_ids: frozenset[str] | None
    mechanism: str
    detail: str = ""
    #: Does this source enumerate every id we could be served? See
    #: ScrapeConfig.absence_is_authoritative — presence and absence are not
    #: symmetric evidence, and a source that documents one route into a
    #: provider says nothing about a model served over another route.
    absence_is_authoritative: bool = True

    @property
    def available(self) -> bool:
        return self.model_ids is not None


@dataclass
class Diff:
    #: (provider, id) in the catalog but absent from a source that enumerates
    #: everything the provider serves. The id is genuinely gone: blocking.
    unknown: list[tuple[str, str]] = field(default_factory=list)
    #: (provider, id) in the catalog but absent from a source that only covers
    #: part of what the provider serves. May be retired, may simply be served
    #: over a route this source does not document — a human has to look.
    unlisted: list[tuple[str, str]] = field(default_factory=list)
    #: (provider, id) offered by the provider but absent from the catalog
    new: list[tuple[str, str]] = field(default_factory=list)
    #: providers that could not be checked at all
    unverifiable: list[ProviderResult] = field(default_factory=list)
    #: (provider, id, verified_at) entries not human-reviewed recently enough
    stale: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def has_blocking_findings(self) -> bool:
        """Only a confirmed-retired id fails the run.

        `unlisted` deliberately does NOT block. Failing on it once already cost
        us a live model: the openai docs scrape does not cover the Codex route
        the boxes actually use, the run went red for gpt-5.5, and the model was
        deleted from the catalog while still being served. A red run demands
        action, so it must only fire on evidence that can carry that weight.
        """
        return bool(self.unknown)

    @property
    def is_empty(self) -> bool:
        return not (
            self.unknown or self.unlisted or self.new or self.unverifiable or self.stale
        )
