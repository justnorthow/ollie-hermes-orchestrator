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
    declined: dict[str, list[str]] | None = None,
) -> Diff:
    """Compare catalog entries against fetched provider model lists.

    `declined` maps a provider to ids we know about and deliberately do not
    offer; they are excluded from `new`. Without it the report re-proposes every
    superseded model the provider still serves, every run, forever.

    It never affects `unknown`: a declined id that somehow ends up in the catalog
    is still a finding, because being on offer in the picker is the thing that
    matters there.
    """
    by_provider = {r.provider: r for r in results}
    diff = Diff()

    # Any provider the catalog references but no result covers is unverifiable.
    # Reported explicitly so a silently-dropped provider cannot read as "clean".
    catalog_providers = {m["provider"] for m in models}
    for provider in sorted(catalog_providers - by_provider.keys()):
        diff.unverifiable.append(
            ProviderResult(provider, None, "none", "no fetch result for provider")
        )

    for result in by_provider.values():
        if not result.available:
            diff.unverifiable.append(result)

    for entry in models:
        provider, model_id = entry["provider"], entry["id"]

        result = by_provider.get(provider)
        if result is not None and result.available and model_id not in result.model_ids:
            # Absence only means "retired" if the source enumerates everything
            # the provider serves. Otherwise it means "not on this page", which
            # is a question, not a verdict — see ProviderResult.
            if result.absence_is_authoritative:
                diff.unknown.append((provider, model_id))
            else:
                diff.unlisted.append((provider, model_id))

        verified = _parse_verified_at(entry.get("verified_at"))
        if verified is None:
            diff.stale.append((provider, model_id, _NEVER))
        elif (today - verified).days > stale_after_days:
            diff.stale.append((provider, model_id, verified.isoformat()))

    catalogued = {(m["provider"], m["id"]) for m in models}
    declined_pairs = {
        (provider, model_id)
        for provider, ids in (declined or {}).items()
        for model_id in ids
    }
    for result in by_provider.values():
        if not result.available:
            continue
        for model_id in sorted(result.model_ids):
            pair = (result.provider, model_id)
            if pair not in catalogued and pair not in declined_pairs:
                diff.new.append(pair)

    return diff
