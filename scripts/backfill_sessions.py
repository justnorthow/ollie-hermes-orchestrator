"""One-time backfill: claim every existing Hermes session for BACKFILL_USER_ID.

Run ON THE BOX (dashboards are loopback-bound):
  BACKFILL_USER_ID=<john-uuid> python3 scripts/backfill_sessions.py [--dry-run]
Requires HERMES_DASHBOARD_URLS, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY in env.
Idempotent — inserts use ignore-duplicates and never overwrite an owner.
"""
import json
import os
import sys

import httpx


def rows_from_sessions(agent: str, sessions: list[dict], user_id: str) -> list[dict]:
    rows = []
    for s in sessions:
        sid = s.get("id")
        if not isinstance(sid, str) or not sid:
            continue
        rows.append({"agent_id": agent, "hermes_session_id": sid,
                     "user_id": user_id, "title": s.get("title") or None})
    return rows


def main() -> int:
    dry = "--dry-run" in sys.argv
    user_id = os.environ.get("BACKFILL_USER_ID", "").strip()
    sb_url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    sb_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    dash_map = json.loads(os.environ.get("HERMES_DASHBOARD_URLS", "{}"))
    if not (user_id and sb_url and sb_key and dash_map):
        print("Missing env: BACKFILL_USER_ID / SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / HERMES_DASHBOARD_URLS")
        return 1
    total = 0
    for agent, base in dash_map.items():
        resp = httpx.get(f"{str(base).rstrip('/')}/api/sessions", params={"limit": 500}, timeout=30.0)
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
