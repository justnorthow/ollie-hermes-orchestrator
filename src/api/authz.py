"""Agent-access authorization: tier (roles.py) x agent scope (agents_json).

Fail-closed. Identity-less callers (no X-Auth-User-Id) are internal/trusted and
are allowed — same trust boundary as the Phase 1 ownership gate.
"""
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

from src.agents_json import read_agents
from src.api import roles

_FORBIDDEN = JSONResponse({"detail": "Forbidden"}, status_code=403)


def can_reach(tier: str, scope: str, manager_visible: bool) -> bool:
    if scope == "user":
        return True  # any authenticated tier
    # scope == "company"
    if roles.is_at_least(tier, "account_admin"):
        return True
    if tier == "manager":
        return bool(manager_visible)
    return False


def _env_path(cfg) -> Path:
    return cfg.hermes_stack_dir / ".env"


def agent_scope(agent_id: str, cfg) -> tuple[str, bool] | None:
    for e in read_agents(_env_path(cfg)):
        if e.id == agent_id:
            return (e.scope, e.manager_visible)
    return None


def _user_id(request: Request) -> str:
    return request.headers.get("X-Auth-User-Id", "").strip()


def check_agent_access(request: Request, agent_id: str, cfg) -> JSONResponse | None:
    """None if allowed; a 403 response if denied. Identity-less -> allowed."""
    user_id = _user_id(request)
    if not user_id:
        return None  # trusted internal caller
    sc = agent_scope(agent_id, cfg)
    if sc is None:
        return _FORBIDDEN  # unknown agent — fail closed, don't leak existence
    scope, manager_visible = sc
    tier = roles.resolve_tier(cfg.instance_id, user_id)
    return None if can_reach(tier, scope, manager_visible) else _FORBIDDEN


def admin_denied(request: Request) -> JSONResponse | None:
    """403 if an IDENTIFIED caller is below account_admin; None otherwise.
    Identity-less callers (no X-Auth-User-Id) are trusted internal → None.
    Config absent (unit tests mounting routers without app.state.config) → None
    (skip; same trust boundary as check_agent_access / _rbac_denied)."""
    user_id = _user_id(request)
    if not user_id:
        return None
    # request.app can KeyError on a bare scope (see runs.py _rbac_denied); guard it.
    try:
        app = request.app
        cfg = getattr(app.state, "config", None)
    except Exception:
        return None
    if cfg is None:
        return None
    tier = roles.resolve_tier(cfg.instance_id, user_id)
    return None if roles.is_at_least(tier, "account_admin") else _FORBIDDEN


def reachable_agent_ids(request: Request, cfg) -> list[str]:
    user_id = _user_id(request)
    entries = read_agents(_env_path(cfg))
    if not user_id:
        return [e.id for e in entries]
    tier = roles.resolve_tier(cfg.instance_id, user_id)
    return [e.id for e in entries if can_reach(tier, e.scope, e.manager_visible)]
