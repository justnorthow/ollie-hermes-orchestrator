import os

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.auth import require_bearer

router = APIRouter(tags=["profile"], dependencies=[Depends(require_bearer)])

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
    email = request.headers.get("X-Auth-Email", "").strip()
    if not email:
        return JSONResponse({"detail": "No authenticated user"}, status_code=401)
    try:
        row = _fetch_profile_row(email, url, key)
    except Exception:
        return JSONResponse({"detail": "Profile lookup failed"}, status_code=502)
    return JSONResponse(content=_normalize(row), headers={"Cache-Control": "no-store"})
