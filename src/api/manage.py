"""Gated per-agent dashboard MANAGEMENT proxy (agent instantiation Phase 2a.2).

Phase 1 routed sessions through the orchestrator; 2a.2 does the same for the
Hermes dashboard's MANAGEMENT surface (skills, cron, config, env [secrets!],
model, profiles, logs, usage, plugins, oauth). Every management call is gated
account_admin+ via authz.admin_denied and constrained to an allowlist of real
dashboard /api/* subtrees, then forwarded to the per-agent Hermes dashboard with
the session token. A separate, ungated status passthrough stays member-reachable
(chat polls it). The raw /hermes-proxy + /dashboard-proxy management paths are
blocked at the nginx edge (frontend repo) so this gated proxy is the only way in.
"""
import logging

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from src.api import authz
from src.api.sessions import _dashboard_base, _dashboard_headers
from src.auth import require_bearer

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["manage"], dependencies=[Depends(require_bearer)])

# Real Hermes dashboard /api/* subtrees the management surface uses, verified
# against HermesDashboardClient. A subpath is allowed iff it equals one of these
# or starts with "<prefix>/". NOTE the nested ones: usage is analytics/usage,
# plugins is dashboard/plugins, memory providers are dashboard/plugin-providers,
# oauth is providers/oauth — a naive first-segment check would be wrong. Bare
# "dashboard"/"providers" are deliberately NOT allowed (too broad). "sessions"
# and "status" are excluded — they have their own routes.
_ALLOWED_PREFIXES = (
    "skills",
    "cron",
    "config",
    "env",
    "model",
    "profiles",
    "logs",
    "analytics",
    "dashboard/plugins",
    "dashboard/plugin-providers",
    "providers/oauth",
)


def _subpath_allowed(subpath: str) -> bool:
    return any(subpath == p or subpath.startswith(p + "/") for p in _ALLOWED_PREFIXES)


def _forward_headers(request: Request) -> dict:
    # Session token (orchestrator->dashboard auth) + pass through the caller's
    # content-type so PUT/POST JSON bodies reach the dashboard correctly.
    headers = dict(_dashboard_headers())
    ct = request.headers.get("content-type")
    if ct:
        headers["content-type"] = ct
    return headers


@router.get("/v1/agents/{agent}/status")
def agent_status(agent: str, request: Request):
    """Member-reachable dashboard status passthrough (chat polls it). Ungated on
    tier by design — deliberately outside the admin management allowlist."""
    base = _dashboard_base(agent)
    if not base:
        return JSONResponse({"detail": "Dashboard proxy not configured"}, status_code=503)
    try:
        resp = httpx.get(f"{base}/api/status", headers=_dashboard_headers(), timeout=30.0)
    except Exception:
        _logger.warning("agent_status upstream failed", exc_info=True)
        return JSONResponse({"detail": "Dashboard unreachable"}, status_code=502)
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type=resp.headers.get("content-type") or "application/json")


@router.api_route(
    "/v1/agents/{agent}/dashboard/{subpath:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def manage(agent: str, subpath: str, request: Request):
    # 1. Gate: account_admin+ (identity-less internal callers pass, per the
    #    established trust boundary).
    denied = authz.admin_denied(request)
    if denied:
        return denied
    # 2. Allowlist (real dashboard paths). Non-allowlisted -> 404 (no existence
    #    leak); the "<prefix>/" match also blocks ../ traversal and sessions/status.
    if not _subpath_allowed(subpath):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    # 3. Forward the original METHOD + body + query to the per-agent dashboard.
    base = _dashboard_base(agent)
    if not base:
        return JSONResponse({"detail": "Dashboard proxy not configured"}, status_code=503)
    qs = request.url.query
    url = f"{base}/api/{subpath}" + (f"?{qs}" if qs else "")
    body = await request.body()
    try:
        resp = httpx.request(
            request.method, url, content=body,
            headers=_forward_headers(request), timeout=30.0,
        )
    except Exception:
        _logger.warning("manage upstream failed", exc_info=True)
        return JSONResponse({"detail": "Dashboard unreachable"}, status_code=502)
    # 4. Return upstream verbatim.
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type=resp.headers.get("content-type") or "application/json")
