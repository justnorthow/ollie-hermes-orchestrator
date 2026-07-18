import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from src.auth import require_bearer

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["profile"], dependencies=[Depends(require_bearer)])

# Writable columns for PUT /v1/profile/mine. user_id and role are deliberately
# excluded: user_id always comes from the trusted X-Auth-User-Id header, and
# role is not member-settable. Never trust the client body for either.
_WRITABLE_FIELDS = {
    "market_area", "title", "brokerage", "license_number", "phone", "email",
    "website", "headshot_url", "logo_url", "display_name",
}

_IMAGE_KINDS = {"headshot", "logo"}

# Brand/scalar fields returned to apps (market_area handled separately as a list).
_FIELDS = (
    "title", "brokerage", "license_number", "phone", "email",
    "website", "headshot_url", "logo_url", "display_name",
)


def _fetch_profile_row(email: str, url: str, key: str) -> dict | None:
    """Call the core Supabase RPC get_profile_by_email (service-role). Returns the
    row dict, or None when no profile matches. Raises on transport/HTTP error."""
    resp = httpx.post(
        f"{url}/rest/v1/rpc/get_profile_by_email",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json={"p_email": email},
        timeout=10.0,
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        return data[0] if data else None
    if isinstance(data, dict):
        return data or None
    return None


def _normalize(row: dict | None) -> dict:
    row = row or {}
    out: dict = {"market_area": row.get("market_area") or []}
    for f in _FIELDS:
        out[f] = row.get(f)
    return out


@router.get("/v1/profile")
def get_profile(request: Request):
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        return JSONResponse({"detail": "Profile lookup not configured"}, status_code=503)
    # SECURITY: X-Auth-Email is TRUSTED here ONLY because this route is protected by
    # require_bearer and the upstream nginx strips any client-supplied X-Auth-* headers
    # and injects X-Auth-Email from the validated session. If that strip is ever missing,
    # a caller could forge the header and fetch any user's profile.
    email = request.headers.get("X-Auth-Email", "").strip()
    if not email:
        return JSONResponse({"detail": "No authenticated user"}, status_code=401)
    try:
        row = _fetch_profile_row(email, url, key)
    except Exception:
        _logger.exception("profile RPC failed for %s", email)
        return JSONResponse({"detail": "Profile lookup failed"}, status_code=502)
    return JSONResponse(content=_normalize(row), headers={"Cache-Control": "no-store"})


def _supabase_creds() -> "tuple[str, str] | None":
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    return (url, key) if url and key else None


def _supabase_public_base() -> "str | None":
    """Browser-facing origin for public storage URLs (mirrors src/api/agents.py:
    SUPABASE_URL is the loopback Kong used for server-side calls, while
    SUPABASE_ISSUER carries the public browser-facing origin)."""
    issuer = os.environ.get("SUPABASE_ISSUER", "").strip()
    if issuer:
        parts = urlsplit(issuer)
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    return url or None


def _trusted_user_id(request: Request) -> str:
    """The authenticated member's Supabase user_id, set by nginx's cryptographic
    auth_request and unforgeable by the browser (mirrors src/api/agents.py)."""
    user_id = request.headers.get("X-Auth-User-Id", "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="not signed in")
    return user_id


@router.get("/v1/profile/mine")
async def get_my_profile(request: Request) -> dict:
    """Member-scoped profile row, keyed by the trusted user_id. Orchestrator-
    mediated via the service role because the self-hosted PostgREST rejects the
    browser's ES256 user token."""
    user_id = _trusted_user_id(request)
    email = request.headers.get("X-Auth-Email", "").strip()
    creds = _supabase_creds()
    if not creds:
        return {"profile": None, "email": email}
    sb_url, key = creds
    resp = await asyncio.to_thread(lambda: httpx.get(
        f"{sb_url}/rest/v1/profiles",
        params={"user_id": f"eq.{user_id}", "select": "*"},
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        timeout=10.0,
    ))
    resp.raise_for_status()
    rows = resp.json()
    profile = rows[0] if rows else None
    return {"profile": profile, "email": email}


@router.put("/v1/profile/mine")
async def put_my_profile(request: Request) -> dict:
    """Member-scoped profile upsert. SECURITY: only whitelisted columns are
    ever forwarded, and user_id is always the trusted header value — any
    client-supplied user_id/role in the body is dropped."""
    user_id = _trusted_user_id(request)
    creds = _supabase_creds()
    if not creds:
        raise HTTPException(status_code=503, detail="supabase not configured")
    sb_url, key = creds
    body = await request.json()
    payload = {k: body[k] for k in _WRITABLE_FIELDS if k in body}
    payload["user_id"] = user_id
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        resp = await asyncio.to_thread(lambda: httpx.post(
            f"{sb_url}/rest/v1/profiles",
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Content-Type": "application/json",
                     "Prefer": "resolution=merge-duplicates,return=minimal"},
            json=payload,
            timeout=10.0,
        ))
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"profile save failed: {exc}")
    return {"ok": True}


@router.post("/v1/profile/image/{kind}")
async def upload_profile_image(kind: str, request: Request) -> dict:
    """Member-scoped headshot/logo upload via the service role (the self-hosted
    storage-api rejects the browser's ES256 user token)."""
    user_id = _trusted_user_id(request)
    if kind not in _IMAGE_KINDS:
        raise HTTPException(status_code=404, detail="unknown image kind")
    creds = _supabase_creds()
    if not creds:
        raise HTTPException(status_code=503, detail="supabase not configured")
    sb_url, key = creds
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="empty body")
    if len(body) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="image too large")
    path = f"{user_id}/{kind}.jpg"
    try:
        resp = await asyncio.to_thread(lambda: httpx.post(
            f"{sb_url}/storage/v1/object/profile-images/{path}",
            content=body,
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Content-Type": "image/jpeg", "x-upsert": "true"},
            timeout=10.0,
        ))
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"image upload failed: {exc}")
    pub = _supabase_public_base() or sb_url
    url = f"{pub}/storage/v1/object/public/profile-images/{path}?t={int(time.time() * 1000)}"
    return {"url": url}
