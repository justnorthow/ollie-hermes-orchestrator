"""Member-scoped user-preferences endpoints (service role, self-hosted ES256 fix).

Mirrors src/api/profile.py: the self-hosted PostgREST rejects the browser's
ES256 user token, so the frontend prefs store's load/save calls must go
through the orchestrator's service role instead, scoped by the trusted
X-Auth-User-Id header (set by nginx's cryptographic auth_request;
unforgeable by the browser).
"""
import asyncio
import logging
import os
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from src.auth import require_bearer

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["prefs"], dependencies=[Depends(require_bearer)])


def _supabase_creds() -> "tuple[str, str] | None":
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    return (url, key) if url and key else None


def _trusted_user_id(request: Request) -> str:
    """The authenticated member's Supabase user_id, set by nginx's cryptographic
    auth_request and unforgeable by the browser (mirrors src/api/profile.py)."""
    user_id = request.headers.get("X-Auth-User-Id", "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="not signed in")
    return user_id


@router.get("/v1/prefs/mine")
async def get_my_prefs(request: Request) -> dict:
    """Member-scoped prefs row, keyed by the trusted user_id. Lenient: a
    signed-out/internal caller simply has no prefs to load (mirrors
    GET /v1/agents/avatars/mine)."""
    user_id = request.headers.get("X-Auth-User-Id", "").strip()
    if not user_id:
        return {"prefs": None}
    creds = _supabase_creds()
    if not creds:
        return {"prefs": None}
    sb_url, key = creds
    try:
        resp = await asyncio.to_thread(lambda: httpx.get(
            f"{sb_url}/rest/v1/user_prefs",
            params={"user_id": f"eq.{user_id}", "select": "prefs"},
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=10.0,
        ))
        resp.raise_for_status()
    except httpx.HTTPError:
        _logger.exception("prefs fetch failed for user_id=%s", user_id)
        raise HTTPException(status_code=502, detail="upstream database error")
    rows = resp.json()
    prefs = rows[0]["prefs"] if rows else None
    return {"prefs": prefs}


@router.put("/v1/prefs/mine")
async def put_my_prefs(request: Request) -> dict:
    """Member-scoped prefs upsert. SECURITY: user_id is always the trusted
    header value — any client-supplied user_id in the body is dropped."""
    user_id = _trusted_user_id(request)
    creds = _supabase_creds()
    if not creds:
        raise HTTPException(status_code=503, detail="supabase not configured")
    sb_url, key = creds
    body = await request.json()
    payload = {
        "user_id": user_id,
        "prefs": body.get("prefs") or {},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        resp = await asyncio.to_thread(lambda: httpx.post(
            f"{sb_url}/rest/v1/user_prefs",
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Content-Type": "application/json",
                     "Prefer": "resolution=merge-duplicates,return=minimal"},
            json=payload,
            timeout=10.0,
        ))
        resp.raise_for_status()
    except httpx.HTTPError:
        _logger.exception("prefs save failed for user_id=%s", user_id)
        raise HTTPException(status_code=502, detail="upstream database error")
    return {"ok": True}
