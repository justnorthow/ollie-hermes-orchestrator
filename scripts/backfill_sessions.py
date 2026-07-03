"""One-time backfill: claim every existing Hermes session for BACKFILL_USER_ID.

Run ON THE BOX (dashboards are loopback-bound):
  BACKFILL_USER_ID=<john-uuid> python3 scripts/backfill_sessions.py [--dry-run]
Requires HERMES_DASHBOARD_URLS, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY in env.
The native Hermes dashboard requires its session token even on loopback, so
HERMES_DASHBOARD_TOKEN (the stable HERMES_DASHBOARD_SESSION_TOKEN set on each
dashboard) must also be in env or /api/sessions returns 401.
Idempotent — inserts use ignore-duplicates and never overwrite an owner.
"""
import json
import os
import sys

import httpx


def _first_str(s: dict, *keys: str) -> str | None:
    """First present, non-empty string value among keys, else None."""
    for key in keys:
        val = s.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def rows_from_sessions(agent: str, sessions: list[dict], user_id: str) -> list[dict]:
    rows = []
    for s in sessions:
        sid = s.get("id")
        if not isinstance(sid, str) or not sid:
            continue
        row = {"agent_id": agent, "hermes_session_id": sid,
               "user_id": user_id, "title": s.get("title") or None}
        # Hermes dashboard /api/sessions items carry camelCase createdAt/updatedAt
        # (see HermesSession in the frontend); handle snake_case variants
        # defensively too. Omit the keys entirely when absent so DB defaults
        # (now()) apply, rather than forcing a scrambled backfill timestamp.
        created_at = _first_str(s, "createdAt", "created_at", "started_at")
        if created_at:
            row["created_at"] = created_at
        last_active_at = _first_str(s, "updatedAt", "updated_at", "last_active", "last_active_at")
        if last_active_at:
            row["last_active_at"] = last_active_at
        rows.append(row)
    return rows


def main() -> int:
    dry = "--dry-run" in sys.argv
    user_id = os.environ.get("BACKFILL_USER_ID", "").strip()
    sb_url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    sb_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    dash_map = json.loads(os.environ.get("HERMES_DASHBOARD_URLS") or "{}")
    if not (user_id and sb_url and sb_key and dash_map):
        print("Missing env: BACKFILL_USER_ID / SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / HERMES_DASHBOARD_URLS")
        return 1
    dash_token = os.environ.get("HERMES_DASHBOARD_TOKEN", "").strip()
    dash_headers = {"X-Hermes-Session-Token": dash_token} if dash_token else {}
    total = 0
    for agent, base in dash_map.items():
        resp = httpx.get(f"{str(base).rstrip('/')}/api/sessions", params={"limit": 500},
                         headers=dash_headers, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()
        sessions = data if isinstance(data, list) else data.get("sessions", [])
        rows = rows_from_sessions(agent, sessions, user_id)
        print(f"{agent}: {len(rows)} sessions")
        total += len(rows)
        if dry or not rows:
            continue
        r = httpx.post(
            f"{sb_url}/rest/v1/agent_sessions",
            params={"on_conflict": "agent_id,hermes_session_id"},
            headers={"apikey": sb_key, "Authorization": f"Bearer {sb_key}",
                     "Content-Type": "application/json",
                     "Prefer": "resolution=ignore-duplicates,return=minimal"},
            json=rows, timeout=30.0,
        )
        r.raise_for_status()
    print(f"{'DRY RUN — ' if dry else ''}total sessions processed: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
