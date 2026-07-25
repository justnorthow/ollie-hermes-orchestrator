"""whoami + admin API for RBAC (Phase 2a). All /v1/admin/* require account_admin+.

User identity (email) for the admin listing comes from the Supabase admin API via
the service role; role/labels come from roles.py. Admin writes emit governance
events (the runs.py _write_event pattern) — role changes are security-relevant.
"""
import logging
import os

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.auth import require_bearer
from src.api import roles, authz

_logger = logging.getLogger(__name__)
router = APIRouter(tags=["admin"], dependencies=[Depends(require_bearer)])

_UNAUTH = JSONResponse({"detail": "Unauthorized"}, status_code=401)
_FORBIDDEN = JSONResponse({"detail": "Forbidden"}, status_code=403)


def _cfg(request):
    return request.app.state.config


def _uid(request) -> str:
    return request.headers.get("X-Auth-User-Id", "").strip()


def _require_admin(request):
    """Return (uid, tier) if caller is account_admin+, else a response to return."""
    uid = _uid(request)
    if not uid:
        return None, _UNAUTH
    tier = roles.resolve_tier(_cfg(request).instance_id, uid)
    if not roles.is_at_least(tier, "account_admin"):
        return None, _FORBIDDEN
    return (uid, tier), None


@router.get("/v1/whoami")
def whoami(request: Request):
    uid = _uid(request)
    if not uid:
        return _UNAUTH
    cfg = _cfg(request)
    tier = roles.resolve_tier(cfg.instance_id, uid)
    label = roles.get_labels(cfg.instance_id).get(tier, tier)
    return {"userId": uid, "tier": tier, "label": label,
            "tags": roles.list_user_tags(uid),
            "governanceView": roles.resolve_governance_view(cfg.instance_id, uid),
            "reachableAgentIds": authz.reachable_agent_ids(request, cfg)}


def _supabase_users() -> dict[str, str]:
    """user_id -> email via the Supabase admin API (service role). Best-effort."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not (url and key):
        return {}
    resp = httpx.get(f"{url}/auth/v1/admin/users", params={"per_page": 200},
                     headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=10.0)
    resp.raise_for_status()
    data = resp.json()
    users = data.get("users", data if isinstance(data, list) else [])
    return {u["id"]: u.get("email", "") for u in users}


@router.get("/v1/admin/users")
def admin_users(request: Request):
    _, deny = _require_admin(request)
    if deny:
        return deny
    cfg = _cfg(request)
    role_map = roles.list_roles(cfg.instance_id)
    labels = roles.get_labels(cfg.instance_id)
    gov_flags = roles.list_governance_flags(cfg.instance_id)
    try:
        emails = _supabase_users()
    except Exception:
        _logger.warning("admin_users: supabase user list failed", exc_info=True)
        emails = {}
    out = []
    for uid, email in emails.items():
        tier = role_map.get(uid, "member")
        out.append({"userId": uid, "email": email, "tier": tier,
                    "label": labels.get(tier, tier), "tags": roles.list_user_tags(uid),
                    "governanceView": gov_flags.get(uid, False)})
    return out


class RoleBody(BaseModel):
    tier: str


@router.put("/v1/admin/users/{user_id}/role")
def set_user_role(user_id: str, body: RoleBody, request: Request):
    caller, deny = _require_admin(request)
    if deny:
        return deny
    caller_uid, caller_tier = caller
    if body.tier not in roles.TIERS:
        return JSONResponse({"detail": "invalid tier"}, status_code=422)
    # Only a platform_operator may mint a platform_operator.
    if body.tier == "platform_operator" and not roles.is_at_least(caller_tier, "platform_operator"):
        return _FORBIDDEN
    target_tier = roles.resolve_tier(_cfg(request).instance_id, user_id)
    # A caller may not modify a user at or above their own tier, nor assign a tier
    # at or above their own — except a platform_operator, who may do anything.
    if not roles.is_at_least(caller_tier, "platform_operator"):
        if roles.is_at_least(target_tier, caller_tier) or roles.is_at_least(body.tier, caller_tier):
            return _FORBIDDEN
    roles.set_tier(_cfg(request).instance_id, user_id, body.tier, caller_uid)
    _emit_admin_event(request, "role.set", user_id, body.tier, caller_uid, caller_tier)
    return {"userId": user_id, "tier": body.tier}


class CreateUserBody(BaseModel):
    email: str
    tier: str
    tags: list[str] = []
    governanceView: bool = False


def _find_auth_user_by_email(email: str) -> dict | None:
    """Return the Supabase auth user dict for email, or None. Service role."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not (url and key):
        return None
    resp = httpx.get(f"{url}/auth/v1/admin/users",
                     params={"per_page": 200},
                     headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=10.0)
    resp.raise_for_status()
    data = resp.json()
    users = data.get("users", data if isinstance(data, list) else [])
    el = email.strip().lower()
    for u in users:
        if (u.get("email") or "").strip().lower() == el:
            return u
    return None


def _generate_link(link_type: str, email: str) -> dict:
    """POST admin/generate_link; returns {"user": {...}, "action_link": ...}.
    Returns the link in the response WITHOUT sending email (SMTP is off)."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    body = {"type": link_type, "email": email}
    site = os.environ.get("SITE_URL", "").strip().rstrip("/")
    if site:
        body["redirect_to"] = f"{site}/invite"
    resp = httpx.post(f"{url}/auth/v1/admin/generate_link",
                      headers={"apikey": key, "Authorization": f"Bearer {key}",
                               "Content-Type": "application/json"},
                      json=body, timeout=10.0)
    resp.raise_for_status()
    return resp.json()


@router.post("/v1/admin/users")
def create_user(body: CreateUserBody, request: Request):
    caller, deny = _require_admin(request)
    if deny:
        return deny
    caller_uid, caller_tier = caller
    cfg = _cfg(request)
    if body.tier not in roles.TIERS:
        return JSONResponse({"detail": "invalid tier"}, status_code=422)
    # Tier guard, identical to set_user_role: non-operators cannot mint at/above self.
    if not roles.is_at_least(caller_tier, "platform_operator"):
        if roles.is_at_least(body.tier, caller_tier):
            return _FORBIDDEN
    if not (os.environ.get("SUPABASE_URL", "").strip() and
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()):
        return JSONResponse({"detail": "Supabase auth not configured on this instance"}, status_code=400)

    email = body.email.strip()
    if not email or "@" not in email:
        return JSONResponse({"detail": "invalid email"}, status_code=422)

    existing = _find_auth_user_by_email(email)
    if existing is not None:
        # "Configured on this instance" == a user_roles row exists for this uid.
        configured = existing["id"] in roles.list_roles(cfg.instance_id)
        if configured:
            return JSONResponse(
                {"detail": "User already exists on this instance — edit them in the table instead."},
                status_code=409)
        link = _generate_link("magiclink", email)  # user exists → magiclink, not invite
    else:
        link = _generate_link("invite", email)     # creates the user + returns link

    # GoTrue's admin/generate_link returns the User fields at the TOP LEVEL
    # (id, email, ... alongside action_link/hashed_token) — there is no nested
    # "user" object. Read the top-level id; fall back to a pre-found existing user.
    user_id = link.get("id") or (existing or {}).get("id")
    if not user_id:
        return JSONResponse({"detail": "could not resolve user id"}, status_code=502)

    roles.set_tier(cfg.instance_id, user_id, body.tier, caller_uid)
    if body.governanceView:
        roles.set_governance_view(cfg.instance_id, user_id, True)
    roles.set_user_tags(user_id, body.tags)
    _emit_admin_event(request, "user.invited", email, body.tier, caller_uid, caller_tier)
    # Return the hashed_token + type (NOT GoTrue's action_link): the self-hosted
    # GoTrue action_link points at {API_EXTERNAL_URL}/verify, which is missing the
    # /auth/v1 prefix Kong routes under (→ "no Route matched"). The caller instead
    # builds a link to the dashboard's /invite?token_hash=…&type=… route, where
    # InviteAccept calls verifyOtp() through the correctly-configured Supabase client.
    return {"userId": user_id, "email": email,
            "tokenHash": link.get("hashed_token", ""),
            "verificationType": link.get("verification_type") or "invite"}


class GovernanceViewBody(BaseModel):
    enabled: bool


class TagsBody(BaseModel):
    tags: list[str]


@router.put("/v1/admin/users/{user_id}/tags")
def set_user_tags_route(user_id: str, body: TagsBody, request: Request):
    caller, deny = _require_admin(request)
    if deny:
        return deny
    roles.set_user_tags(user_id, body.tags)
    _emit_admin_event(request, "tags.set", user_id, ",".join(body.tags), caller[0], caller[1])
    return {"userId": user_id, "tags": body.tags}


@router.put("/v1/admin/users/{user_id}/governance-view")
def set_user_governance_view(user_id: str, body: GovernanceViewBody, request: Request):
    caller, deny = _require_admin(request)
    if deny:
        return deny
    roles.set_governance_view(_cfg(request).instance_id, user_id, body.enabled)
    _emit_admin_event(request, "governance_view.set", user_id, str(body.enabled),
                      caller[0], caller[1])
    return {"userId": user_id, "governanceView": body.enabled}


@router.get("/v1/admin/role-labels")
def get_role_labels(request: Request):
    _, deny = _require_admin(request)
    if deny:
        return deny
    return roles.get_labels(_cfg(request).instance_id)


@router.put("/v1/admin/role-labels")
def put_role_labels(body: dict, request: Request):
    caller, deny = _require_admin(request)
    if deny:
        return deny
    labels = {t: str(l) for t, l in body.items() if t in roles.TIERS}
    roles.set_labels(_cfg(request).instance_id, labels)
    _emit_admin_event(request, "role_labels.set", None, ",".join(labels), caller[0], caller[1])
    return roles.get_labels(_cfg(request).instance_id)


def _emit_admin_event(request, event_type, target_user, detail, actor, actor_tier) -> None:
    """Best-effort governance event for an admin write. Never raises."""
    try:
        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not (url and key):
            return
        try:
            instance_id = request.app.state.config.instance_id
        except Exception:
            instance_id = None
        httpx.post(
            f"{url}/rest/v1/governance_events",
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Content-Type": "application/json", "Prefer": "return=minimal"},
            json={"user_email": actor or "", "user_role": actor_tier,
                  "app": "admin", "event_type": event_type, "status": "ok",
                  "title": target_user, "findings": [], "content": detail,
                  "run_id": None, "instance_id": instance_id},
            timeout=10.0,
        ).raise_for_status()
    except Exception:
        _logger.warning("_emit_admin_event failed", exc_info=True)
