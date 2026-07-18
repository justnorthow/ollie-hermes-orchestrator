"""Compliance/governance/TRAIGA endpoints (service role).

The Review, Compliance, Verification, and TRAIGA Report pages used to read/write
Supabase directly from the browser; the self-hosted PostgREST rejects the
browser's ES256 user token, so these calls are routed through the orchestrator
service role instead. Authz that RLS used to provide is re-enforced here via
_compliance_denied, mirroring the DB's old governance_events RLS + the frontend
RoleRoute OR-gate: allow if the caller carries the global 'compliance' tag
(cross-instance oversight) OR resolves governance_view on this instance
(account_admin+ tier OR the explicit flag).

Identity comes from X-Auth-User-Id (nginx-set, spoof-proof, unforgeable by the
browser). The caller's email for write-stamping (p_verified_by) comes from
X-Auth-Email, also nginx-set.
"""
import logging

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.api import roles
from src.api.agents import _supabase_creds
from src.auth import require_bearer

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["compliance"], dependencies=[Depends(require_bearer)])

_NOT_CONFIGURED = JSONResponse({"error": "supabase not configured"}, status_code=503)


def _compliance_denied(request: Request) -> "JSONResponse | None":
    uid = request.headers.get("X-Auth-User-Id", "").strip()
    if not uid:
        return JSONResponse({"error": "not signed in"}, status_code=401)
    cfg = request.app.state.config
    # OR-gate matching governance_events RLS: global 'compliance' tag (cross-instance
    # oversight) OR own-instance account_admin+/governance_view.
    allowed = ("compliance" in roles.list_user_tags(uid)) or roles.resolve_governance_view(cfg.instance_id, uid)
    if not allowed:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return None


def _compliance_write_denied(request: Request) -> "JSONResponse | None":
    """Stricter gate for mutating endpoints. governance_view is a READ-oversight
    grant (mirrors the DB's old governance_events RLS) and must NOT by itself
    authorize approving/rejecting compliance rules or toggling auto-approve;
    only the global 'compliance' tag or an account_admin+ tier may write."""
    uid = request.headers.get("X-Auth-User-Id", "").strip()
    if not uid:
        return JSONResponse({"error": "not signed in"}, status_code=401)
    cfg = request.app.state.config
    tier = roles.resolve_tier(cfg.instance_id, uid)
    allowed = ("compliance" in roles.list_user_tags(uid)) or roles.is_at_least(tier, "account_admin")
    if not allowed:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return None


def _sb_headers(key: str) -> dict:
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _verified_by(request: Request) -> "str | None":
    return request.headers.get("X-Auth-Email", "").strip() or None


@router.get("/v1/governance/events")
def governance_events(request: Request):
    denied = _compliance_denied(request)
    if denied:
        return denied
    creds = _supabase_creds()
    if not creds:
        return _NOT_CONFIGURED
    sb, key = creds
    try:
        resp = httpx.get(
            f"{sb}/rest/v1/governance_events",
            params={"select": "*", "order": "created_at.desc", "limit": "1000"},
            headers=_sb_headers(key), timeout=10.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        _logger.exception("governance_events fetch failed")
        return JSONResponse({"error": "upstream database error"}, status_code=502)
    events = resp.json()
    return {"events": events, "capped": len(events) >= 1000}


@router.get("/v1/compliance/rules")
def list_rules(request: Request):
    denied = _compliance_denied(request)
    if denied:
        return denied
    creds = _supabase_creds()
    if not creds:
        return _NOT_CONFIGURED
    sb, key = creds
    params = {"select": "*", "order": "confidence.asc,rule_key.asc", "limit": "1000"}
    for q in ("status", "confidence", "hub"):
        v = request.query_params.get(q)
        if v:
            params[q] = f"eq.{v}"
    try:
        resp = httpx.get(
            f"{sb}/rest/v1/compliance_rules", params=params,
            headers=_sb_headers(key), timeout=10.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        _logger.exception("compliance_rules fetch failed")
        return JSONResponse({"error": "upstream database error"}, status_code=502)
    rules = resp.json()
    return {"rules": rules, "capped": len(rules) >= 1000}


@router.get("/v1/compliance/config")
def get_config(request: Request):
    denied = _compliance_denied(request)
    if denied:
        return denied
    creds = _supabase_creds()
    if not creds:
        return _NOT_CONFIGURED
    sb, key = creds
    try:
        resp = httpx.get(
            f"{sb}/rest/v1/compliance_config",
            params={"id": "eq.1", "select": "auto_approve"},
            headers=_sb_headers(key), timeout=10.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        _logger.exception("compliance_config fetch failed")
        return JSONResponse({"error": "upstream database error"}, status_code=502)
    rows = resp.json()
    aa = (rows[0]["auto_approve"] if rows else {}) or {}
    return {"high": bool(aa.get("high")), "medium": bool(aa.get("medium"))}


class ReviewBody(BaseModel):
    ruleKeys: list[str] = []
    decision: str
    note: "str | None" = None


@router.post("/v1/compliance/review")
def review(body: ReviewBody, request: Request):
    denied = _compliance_write_denied(request)
    if denied:
        return denied
    if body.decision not in ("verified", "rejected"):
        return JSONResponse({"error": "invalid decision"}, status_code=400)
    if not body.ruleKeys:
        return {"count": 0}
    creds = _supabase_creds()
    if not creds:
        return _NOT_CONFIGURED
    sb, key = creds
    try:
        resp = httpx.post(
            f"{sb}/rest/v1/rpc/review_rules",
            json={"p_rule_keys": body.ruleKeys, "p_decision": body.decision,
                  "p_note": body.note, "p_verified_by": _verified_by(request)},
            headers=_sb_headers(key), timeout=10.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        _logger.exception("compliance review failed")
        return JSONResponse({"error": "upstream database error"}, status_code=502)
    return {"count": resp.json()}


class AutoApproveBody(BaseModel):
    tier: str
    enabled: bool


@router.post("/v1/compliance/auto-approve")
def auto_approve(body: AutoApproveBody, request: Request):
    denied = _compliance_write_denied(request)
    if denied:
        return denied
    if body.tier not in ("high", "medium"):
        return JSONResponse({"error": "low is never auto-approvable"}, status_code=400)
    creds = _supabase_creds()
    if not creds:
        return _NOT_CONFIGURED
    sb, key = creds
    try:
        resp = httpx.post(
            f"{sb}/rest/v1/rpc/set_auto_approve",
            json={"p_tier": body.tier, "p_enabled": body.enabled, "p_verified_by": _verified_by(request)},
            headers=_sb_headers(key), timeout=10.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        _logger.exception("compliance auto-approve failed")
        return JSONResponse({"error": "upstream database error"}, status_code=502)
    return {"count": resp.json()}


@router.get("/v1/traiga/readiness")
def traiga_readiness(request: Request):
    denied = _compliance_denied(request)
    if denied:
        return denied
    from_ = request.query_params.get("from", "").strip()
    to = request.query_params.get("to", "").strip()
    if not from_ or not to:
        return JSONResponse({"error": "from and to are required"}, status_code=400)
    creds = _supabase_creds()
    if not creds:
        return _NOT_CONFIGURED
    sb, key = creds
    try:
        counts_resp = httpx.post(
            f"{sb}/rest/v1/rpc/traiga_readiness_counts",
            json={"p_from": from_, "p_to": to}, headers=_sb_headers(key), timeout=10.0,
        )
        counts_resp.raise_for_status()
        window_resp = httpx.post(
            f"{sb}/rest/v1/rpc/traiga_readiness_window",
            json={"p_from": from_, "p_to": to}, headers=_sb_headers(key), timeout=10.0,
        )
        window_resp.raise_for_status()
    except httpx.HTTPError:
        _logger.exception("traiga readiness failed")
        return JSONResponse({"error": "upstream database error"}, status_code=502)
    # PostgREST returns count(*)::bigint as a JSON string on these boxes; coerce
    # to int here so the frontend (and the regulator-facing TRAIGA report) don't
    # do string math ("0"+"3") or string comparisons ("0"===0) on these counts.
    counts = []
    for row in counts_resp.json():
        row = dict(row)
        row["n"] = int(row["n"])
        counts.append(row)
    win = window_resp.json()
    if win:
        window = dict(win[0])
        window["total"] = int(window["total"])
    else:
        window = {"total": 0, "first_at": None, "last_at": None}
    return {"counts": counts, "window": window}
