"""Owner-filtered session endpoints + the session-ownership store.

Agent instantiation Phase 1 (spec 2026-07-03 §5): sessions are owned by the
Supabase user who created them. The Hermes dashboard's own /api/sessions is
unfiltered, so the frontend now reads sessions ONLY through these endpoints.
Ownership rows live in Supabase `agent_sessions`, written via the service role
(same PostgREST pattern as governance_events in runs.py).
"""
import json
import logging
import os

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from src.auth import require_bearer

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["sessions"], dependencies=[Depends(require_bearer)])

_NOT_FOUND = JSONResponse({"detail": "Session not found"}, status_code=403)


def _dashboard_base(agent: str) -> str | None:
    """Per-agent Hermes dashboard base URL. HERMES_DASHBOARD_URLS is a JSON map
    {agent: url} (mirrors HERMES_GATEWAY_URLS in runs.py)."""
    raw = os.environ.get("HERMES_DASHBOARD_URLS", "").strip()
    if not raw:
        return None
    try:
        url = json.loads(raw).get(agent)
    except (ValueError, AttributeError):
        _logger.warning("HERMES_DASHBOARD_URLS is not a valid JSON object")
        return None
    return str(url).rstrip("/") if url else None


def _sb() -> tuple[str, str] | None:
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    return (url, key) if url and key else None


def _sb_headers(key: str) -> dict:
    return {"apikey": key, "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"}


def get_session_owner(agent: str, session_id: str) -> str | None:
    """user_id owning (agent, session), or None if unowned/unknown/store unavailable."""
    sb = _sb()
    if not sb:
        return None
    url, key = sb
    try:
        resp = httpx.get(
            f"{url}/rest/v1/agent_sessions",
            params={"agent_id": f"eq.{agent}", "hermes_session_id": f"eq.{session_id}",
                    "select": "user_id"},
            headers=_sb_headers(key), timeout=10.0,
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0]["user_id"] if rows else None
    except Exception:
        _logger.warning("get_session_owner failed", exc_info=True)
        return None


def record_session(agent: str, session_id: str, user_id: str) -> None:
    """Insert an ownership row if absent. Best-effort: never raises, never overwrites."""
    sb = _sb()
    if not sb:
        return
    url, key = sb
    try:
        resp = httpx.post(
            f"{url}/rest/v1/agent_sessions",
            params={"on_conflict": "agent_id,hermes_session_id"},
            headers={**_sb_headers(key),
                     "Prefer": "resolution=ignore-duplicates,return=minimal"},
            json={"agent_id": agent, "hermes_session_id": session_id, "user_id": user_id},
            timeout=10.0,
        )
        resp.raise_for_status()
    except Exception:
        _logger.warning("record_session failed", exc_info=True)


def _list_user_rows(agent: str, user_id: str) -> list[dict]:
    sb = _sb()
    if not sb:
        return []
    url, key = sb
    resp = httpx.get(
        f"{url}/rest/v1/agent_sessions",
        params={"agent_id": f"eq.{agent}", "user_id": f"eq.{user_id}",
                "select": "hermes_session_id,title,created_at,last_active_at",
                "order": "last_active_at.desc", "limit": "100"},
        headers=_sb_headers(key), timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()


def _delete_row(agent: str, session_id: str) -> None:
    sb = _sb()
    if not sb:
        return
    url, key = sb
    try:
        httpx.delete(
            f"{url}/rest/v1/agent_sessions",
            params={"agent_id": f"eq.{agent}", "hermes_session_id": f"eq.{session_id}"},
            headers=_sb_headers(key), timeout=10.0,
        ).raise_for_status()
    except Exception:
        _logger.warning("_delete_row failed", exc_info=True)


def _dashboard_get(agent: str, path: str) -> tuple[int, bytes]:
    base = _dashboard_base(agent)
    resp = httpx.get(f"{base}{path}", timeout=30.0)
    return resp.status_code, resp.content


def _dashboard_delete(agent: str, path: str) -> tuple[int, bytes]:
    base = _dashboard_base(agent)
    resp = httpx.delete(f"{base}{path}", timeout=30.0)
    return resp.status_code, resp.content


def _identity(request: Request) -> str:
    return request.headers.get("X-Auth-User-Id", "").strip()


@router.get("/v1/sessions/{agent}")
def list_sessions(agent: str, request: Request):
    user_id = _identity(request)
    if not user_id:
        return _NOT_FOUND
    try:
        rows = _list_user_rows(agent, user_id)
    except Exception:
        _logger.warning("list_sessions store read failed", exc_info=True)
        return JSONResponse({"detail": "Session store unavailable"}, status_code=503)
    return [
        {"id": r["hermes_session_id"], "title": r.get("title"),
         "createdAt": r.get("created_at"), "lastActiveAt": r.get("last_active_at")}
        for r in rows
    ]


@router.get("/v1/sessions/{agent}/{session_id}/messages")
def session_messages(agent: str, session_id: str, request: Request):
    user_id = _identity(request)
    if not user_id or get_session_owner(agent, session_id) != user_id:
        return _NOT_FOUND
    if not _dashboard_base(agent):
        return JSONResponse({"detail": "Dashboard proxy not configured"}, status_code=503)
    status, content = _dashboard_get(agent, f"/api/sessions/{session_id}/messages")
    return Response(content=content, status_code=status, media_type="application/json")


@router.delete("/v1/sessions/{agent}/{session_id}")
def delete_session(agent: str, session_id: str, request: Request):
    user_id = _identity(request)
    if not user_id or get_session_owner(agent, session_id) != user_id:
        return _NOT_FOUND
    if not _dashboard_base(agent):
        return JSONResponse({"detail": "Dashboard proxy not configured"}, status_code=503)
    status, content = _dashboard_delete(agent, f"/api/sessions/{session_id}")
    _delete_row(agent, session_id)
    return Response(content=content, status_code=status, media_type="application/json")
