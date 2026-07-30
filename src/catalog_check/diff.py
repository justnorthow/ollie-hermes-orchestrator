"""Pure diff between the catalog and provider model lists.

No I/O. Provider lists arrive as ProviderResult objects from whatever
mechanism fetched them, so this module is identical under scraping and under
an authenticated API.
"""
from datetime import date, datetime

from src.catalog_check.types import Diff, ProviderResult

_NEVER = "never"


def _parse_verified_at(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def compute_diff(
    models: list[dict],
    results: list[ProviderResult],
    today: date,
    stale_after_days: int = 365,
) -> Diff:
    """Compare catalog entries against fetched provider model lists."""
    by_provider = {r.provider: r for r in results}
    diff = Diff()

    # Any provider the catalog references but no result covers is unverifiable.
    # Reported explicitly so a silently-dropped provider cannot read as "clean".
    catalog_providers = {m["provider"] for m in models}
    for provider in sorted(catalog_providers - by_provider.keys()):
        diff.unverifiable.append(
            ProviderResult(provider, None, "none", "no fetch result for provider")
        )

    for result in results:
        if not result.available:
            diff.unverifiable.append(result)

    for entry in models:
        provider, model_id = entry["provider"], entry["id"]

        result = by_provider.get(provider)
        if result is not None and result.available and model_id not in result.model_ids:
            diff.unknown.append((provider, model_id))

        verified = _parse_verified_at(entry.get("verified_at"))
        if verified is None:
            diff.stale.append((provider, model_id, _NEVER))
        elif (today - verified).days > stale_after_days:
            diff.stale.append((provider, model_id, verified.isoformat()))

    catalogued = {(m["provider"], m["id"]) for m in models}
    for result in results:
        if not result.available:
            continue
        for model_id in sorted(result.model_ids):
            if (result.provider, model_id) not in catalogued:
                diff.new.append((result.provider, model_id))

    return diff
