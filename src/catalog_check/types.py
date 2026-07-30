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

    @property
    def available(self) -> bool:
        return self.model_ids is not None


@dataclass
class Diff:
    #: (provider, id) present in the catalog but absent from the provider list
    unknown: list[tuple[str, str]] = field(default_factory=list)
    #: (provider, id) offered by the provider but absent from the catalog
    new: list[tuple[str, str]] = field(default_factory=list)
    #: providers that could not be checked at all
    unverifiable: list[ProviderResult] = field(default_factory=list)
    #: (provider, id, verified_at) entries not human-reviewed recently enough
    stale: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def has_blocking_findings(self) -> bool:
        """Only an unknown id fails the run. A new model must not turn CI red."""
        return bool(self.unknown)

    @property
    def is_empty(self) -> bool:
        return not (self.unknown or self.new or self.unverifiable or self.stale)
