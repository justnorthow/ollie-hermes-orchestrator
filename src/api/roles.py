"""RBAC tier model + Supabase-backed store + cached resolution (Phase 2a).

Canonical tiers are fixed and ordered; labels are cosmetic. `user_roles` is the
source of truth (instance-scoped). resolve_tier() is fail-closed to 'member'.
Writes use the service role via PostgREST (the _write_event pattern in runs.py).
"""
import logging
import os
import time

import httpx

_logger = logging.getLogger(__name__)

TIERS: tuple[str, ...] = ("member", "manager", "account_admin", "platform_operator")
_RANK = {t: i for i, t in enumerate(TIERS)}
DEFAULT_LABELS: dict[str, str] = {
    "member": "Member",
    "manager": "Manager",
    "account_admin": "Account Admin",
    "platform_operator": "JNOW Operator",
}

_CACHE_TTL = 30.0  # seconds
# (instance_id, user_id) -> (tier, monotonic_expiry)
_tier_cache: dict[tuple[str, str], tuple[str, float]] = {}
# (instance_id, user_id) -> (governance_view_bool, monotonic_expiry)
_gov_cache: dict[tuple[str, str], tuple[bool, float]] = {}


def is_at_least(tier: str, minimum: str) -> bool:
    return _RANK.get(tier, -1) >= _RANK.get(minimum, len(TIERS))


def _sb() -> tuple[str, str] | None:
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    return (url, key) if url and key else None


def _sb_headers(key: str) -> dict:
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _fetch_tier(instance_id: str, user_id: str) -> str | None:
    sb = _sb()
    if not sb:
        return None
    url, key = sb
    resp = httpx.get(
        f"{url}/rest/v1/user_roles",
        params={"instance_id": f"eq.{instance_id}", "user_id": f"eq.{user_id}", "select": "tier"},
        headers=_sb_headers(key), timeout=10.0,
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0]["tier"] if rows else None


def resolve_tier(instance_id: str, user_id: str) -> str:
    """Caller's tier, cached; fail-closed to 'member' on absence or any error."""
    now = time.monotonic()
    key = (instance_id, user_id)
    hit = _tier_cache.get(key)
    if hit and hit[1] > now:
        return hit[0]
    try:
        tier = _fetch_tier(instance_id, user_id) or "member"
        if tier not in _RANK:
            tier = "member"
    except Exception:
        _logger.warning("resolve_tier failed; defaulting member", exc_info=True)
        tier = "member"
    _tier_cache[key] = (tier, now + _CACHE_TTL)
    return tier


def _fetch_gov(instance_id: str, user_id: str) -> bool:
    """True iff the user's user_roles row for instance_id grants governance view:
    account_admin+ tier OR the governance_view flag. False if no row."""
    sb = _sb()
    if not sb:
        return False
    url, key = sb
    resp = httpx.get(
        f"{url}/rest/v1/user_roles",
        params={"instance_id": f"eq.{instance_id}", "user_id": f"eq.{user_id}",
                "select": "tier,governance_view"},
        headers=_sb_headers(key), timeout=10.0,
    )
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return False
    row = rows[0]
    return is_at_least(row.get("tier") or "member", "account_admin") or bool(row.get("governance_view"))


def resolve_governance_view(instance_id: str, user_id: str) -> bool:
    """Whether the caller may see this instance's governance events; cached;
    fail-closed to False on absence or any error."""
    now = time.monotonic()
    key = (instance_id, user_id)
    hit = _gov_cache.get(key)
    if hit and hit[1] > now:
        return hit[0]
    try:
        gv = _fetch_gov(instance_id, user_id)
    except Exception:
        _logger.warning("resolve_governance_view failed; defaulting False", exc_info=True)
        gv = False
    _gov_cache[key] = (gv, now + _CACHE_TTL)
    return gv


def invalidate_cache(user_id: str | None = None) -> None:
    if user_id is None:
        _tier_cache.clear()
        _gov_cache.clear()
    else:
        # cache is keyed by (instance_id, user_id); sweep all instances for this user
        for k in [k for k in _tier_cache if k[1] == user_id]:
            _tier_cache.pop(k, None)
        for k in [k for k in _gov_cache if k[1] == user_id]:
            _gov_cache.pop(k, None)


def set_tier(instance_id: str, user_id: str, tier: str, assigned_by: str | None) -> None:
    if tier not in _RANK:
        raise ValueError(f"invalid tier: {tier}")
    sb = _sb()
    if not sb:
        raise RuntimeError("Supabase not configured")
    url, key = sb
    httpx.post(
        f"{url}/rest/v1/user_roles",
        params={"on_conflict": "instance_id,user_id"},
        headers={**_sb_headers(key), "Prefer": "resolution=merge-duplicates,return=minimal"},
        json={"instance_id": instance_id, "user_id": user_id, "tier": tier,
              "assigned_by": assigned_by, "updated_at": _now_iso()},
        timeout=10.0,
    ).raise_for_status()
    invalidate_cache(user_id)


def set_governance_view(instance_id: str, user_id: str, enabled: bool) -> None:
    """Set a user's per-instance governance_view flag WITHOUT clobbering their tier.
    Ensure a row exists (on_conflict do nothing, tier=member), then PATCH the flag."""
    sb = _sb()
    if not sb:
        raise RuntimeError("Supabase not configured")
    url, key = sb
    # 1. Ensure a row exists; on conflict do nothing so an existing tier is untouched.
    httpx.post(
        f"{url}/rest/v1/user_roles",
        params={"on_conflict": "instance_id,user_id"},
        headers={**_sb_headers(key), "Prefer": "resolution=ignore-duplicates,return=minimal"},
        json={"instance_id": instance_id, "user_id": user_id, "tier": "member"},
        timeout=10.0,
    ).raise_for_status()
    # 2. Set the flag only (never touches tier).
    httpx.patch(
        f"{url}/rest/v1/user_roles",
        params={"instance_id": f"eq.{instance_id}", "user_id": f"eq.{user_id}"},
        headers={**_sb_headers(key), "Prefer": "return=minimal"},
        json={"governance_view": enabled, "updated_at": _now_iso()},
        timeout=10.0,
    ).raise_for_status()
    invalidate_cache(user_id)


def list_roles(instance_id: str) -> dict[str, str]:
    sb = _sb()
    if not sb:
        return {}
    url, key = sb
    resp = httpx.get(
        f"{url}/rest/v1/user_roles",
        params={"instance_id": f"eq.{instance_id}", "select": "user_id,tier"},
        headers=_sb_headers(key), timeout=10.0,
    )
    resp.raise_for_status()
    return {r["user_id"]: r["tier"] for r in resp.json()}


def list_governance_flags(instance_id: str) -> dict[str, bool]:
    """user_id -> raw governance_view flag for this instance; {} if unconfigured.
    The RAW column value (not the tier-resolved view) — the admin UI toggle owns
    this flag directly, and derives the tier-grant separately."""
    sb = _sb()
    if not sb:
        return {}
    url, key = sb
    resp = httpx.get(
        f"{url}/rest/v1/user_roles",
        params={"instance_id": f"eq.{instance_id}", "select": "user_id,governance_view"},
        headers=_sb_headers(key), timeout=10.0,
    )
    resp.raise_for_status()
    return {r["user_id"]: bool(r.get("governance_view")) for r in resp.json()}


def _fetch_labels(instance_id: str) -> dict[str, str]:
    sb = _sb()
    if not sb:
        return {}
    url, key = sb
    resp = httpx.get(
        f"{url}/rest/v1/role_labels",
        params={"instance_id": f"eq.{instance_id}", "select": "tier,label"},
        headers=_sb_headers(key), timeout=10.0,
    )
    resp.raise_for_status()
    return {r["tier"]: r["label"] for r in resp.json()}


def get_labels(instance_id: str) -> dict[str, str]:
    merged = dict(DEFAULT_LABELS)
    try:
        merged.update({t: l for t, l in _fetch_labels(instance_id).items() if t in _RANK})
    except Exception:
        _logger.warning("get_labels failed; using defaults", exc_info=True)
    return merged


def set_labels(instance_id: str, labels: dict[str, str]) -> None:
    sb = _sb()
    if not sb:
        raise RuntimeError("Supabase not configured")
    url, key = sb
    rows = [{"instance_id": instance_id, "tier": t, "label": str(l)[:60]}
            for t, l in labels.items() if t in _RANK]
    if not rows:
        return
    httpx.post(
        f"{url}/rest/v1/role_labels",
        params={"on_conflict": "instance_id,tier"},
        headers={**_sb_headers(key), "Prefer": "resolution=merge-duplicates,return=minimal"},
        json=rows, timeout=10.0,
    ).raise_for_status()


def _now_iso() -> str:
    # UTC ISO-8601; imported lazily so tests can monkeypatch time without import churn.
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# --- Global user tags (functional attribute of the person; no instance_id) ---

_tags_cache: dict[str, tuple[list[str], float]] = {}  # user_id -> (tags, expiry)


def _fetch_tags(user_id: str) -> list[str]:
    sb = _sb()
    if not sb:
        return []
    url, key = sb
    resp = httpx.get(
        f"{url}/rest/v1/user_tags",
        params={"user_id": f"eq.{user_id}", "select": "tag"},
        headers=_sb_headers(key), timeout=10.0,
    )
    resp.raise_for_status()
    return sorted(r["tag"] for r in resp.json())


def list_user_tags(user_id: str) -> list[str]:
    """GLOBAL functional tags for a user; cached; fail-closed to [] on any error."""
    now = time.monotonic()
    hit = _tags_cache.get(user_id)
    if hit and hit[1] > now:
        return hit[0]
    try:
        tags = _fetch_tags(user_id)
    except Exception:
        _logger.warning("list_user_tags failed; defaulting []", exc_info=True)
        tags = []
    _tags_cache[user_id] = (tags, now + _CACHE_TTL)
    return tags


def invalidate_tags(user_id: str | None = None) -> None:
    if user_id is None:
        _tags_cache.clear()
    else:
        _tags_cache.pop(user_id, None)


def delete_user_rows(instance_id: str, user_id: str) -> None:
    """Remove a user's instance role row + all their global tags. Service role."""
    sb = _sb()
    if not sb:
        raise RuntimeError("Supabase not configured")
    url, key = sb
    httpx.delete(
        f"{url}/rest/v1/user_roles",
        params={"instance_id": f"eq.{instance_id}", "user_id": f"eq.{user_id}"},
        headers=_sb_headers(key), timeout=10.0,
    ).raise_for_status()
    httpx.delete(
        f"{url}/rest/v1/user_tags",
        params={"user_id": f"eq.{user_id}"},
        headers=_sb_headers(key), timeout=10.0,
    ).raise_for_status()
    invalidate_cache(user_id)
    invalidate_tags(user_id)


def set_user_tags(user_id: str, tags: list[str]) -> None:
    """Replace a user's global tags (delete-all + insert). Service role."""
    sb = _sb()
    if not sb:
        raise RuntimeError("Supabase not configured")
    url, key = sb
    # Delete existing, then insert the new set (small sets; simplest correct).
    httpx.delete(
        f"{url}/rest/v1/user_tags",
        params={"user_id": f"eq.{user_id}"},
        headers=_sb_headers(key), timeout=10.0,
    ).raise_for_status()
    clean = [t for t in {str(x).strip() for x in tags} if t]
    if clean:
        httpx.post(
            f"{url}/rest/v1/user_tags",
            headers={**_sb_headers(key), "Prefer": "return=minimal"},
            json=[{"user_id": user_id, "tag": t} for t in clean],
            timeout=10.0,
        ).raise_for_status()
    invalidate_tags(user_id)
