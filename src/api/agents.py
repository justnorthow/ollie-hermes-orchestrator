import logging
import os
import time

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from src.agents_json import read_agents
from src.api import authz
from src.auth import require_bearer
from src.audit import audit
from src.config import Config
from src.docker_ops import bounce_dashboard
from src.identity import resolve_soul_path, soul_needs_identity, write_soul
from src.lifecycle import CreateRequest, UpdateRequest, create_agent, delete_agent, update_agent
from src.models import Agent, CreateAgent, SetIdentityRequest, UpdateAgent
from src.persona_polish import polish_persona
from src.profile_ops import get_profile_model
from src.sse import sse_event

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/agents", tags=["agents"], dependencies=[Depends(require_bearer)])


def _entry_to_agent(e, cfg: Config | None = None) -> dict:
    needs_identity = False
    model = e.model
    if cfg is not None:
        soul_path = resolve_soul_path(e.id, cfg.hermes_home, cfg.hermes_profiles_dir)
        needs_identity = soul_needs_identity(soul_path)
        model = get_profile_model(e.id, cfg.hermes_home, cfg.hermes_profiles_dir) or model
    return Agent(
        id=e.id, displayName=e.name, color=e.color,
        provider="anthropic", model=model or "unknown",
        gatewayPort=e.gateway_port, dashboardPort=e.dashboard_port,
        needsIdentity=needs_identity, subtitle=e.subtitle, avatar_url=e.avatar_url,
    ).model_dump()


@router.get("")
async def list_agents(request: Request) -> dict:
    cfg = request.app.state.config
    entries = read_agents(cfg.hermes_stack_dir / ".env")
    reachable = set(authz.reachable_agent_ids(request, cfg))
    return {"agents": [_entry_to_agent(e, cfg) for e in entries if e.id in reachable]}


@router.get("/{agent_id}")
async def get_agent(agent_id: str, request: Request) -> dict:
    cfg = request.app.state.config
    denied = authz.check_agent_access(request, agent_id, cfg)
    if denied:
        return denied
    entries = read_agents(cfg.hermes_stack_dir / ".env")
    e = next((x for x in entries if x.id == agent_id), None)
    if not e:
        raise HTTPException(status_code=404, detail="not_found")
    return _entry_to_agent(e, cfg)


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create(body: CreateAgent, request: Request) -> StreamingResponse:
    denied = authz.admin_denied(request)
    if denied:
        return denied
    cfg = request.app.state.config
    api_key = request.app.state.hermes_gateway_key
    actor_ip = request.client.host if request.client else "unknown"

    req = CreateRequest(
        name=body.name,
        display_name=body.displayName,
        color=body.color,
        provider=body.provider,
        model=body.model,
        api_key=body.apiKey,
        system_prompt=body.systemPrompt,
        enabled_skills=body.enabledSkills,
        api_server_key=api_key,
        auth_method=body.authMethod,
        subtitle=body.subtitle,
        avatar_url=body.avatar_url,
    )

    async def stream():
        result_event = None
        async for ev in create_agent(req):
            if ev.get("event") in ("done", "error"):
                result_event = ev
                yield sse_event(event=ev["event"], data=ev)
            else:
                yield sse_event(event="progress", data=ev)
        result = "ok" if (result_event or {}).get("event") == "done" else "error"
        duration = (result_event or {}).get("duration_ms", 0)
        audit(cfg.audit_log_path, op="create", agent_id=body.name,
              actor_ip=actor_ip, result=result, duration_ms=duration,
              error=(result_event or {}).get("error"))

    return StreamingResponse(stream(), media_type="text/event-stream", status_code=202)


def _bounce_after_delete(cfg, actor_ip: str, agent_id: str) -> None:
    """Runs as a BackgroundTask after the DELETE's 204 has been sent, so
    bouncing the dashboard container (which houses the nginx that proxied
    this very request) can't sever the in-flight response — the browser saw
    a 502 for a successful delete when the bounce was inline (sandbox 'pam',
    2026-07-17). Mirrors instance.py's _bounce_after_write. Must never raise:
    a raising background task poisons the request in tests and logs."""
    try:
        bounce_dashboard()
    except Exception as e:
        _logger.warning("delete: deferred dashboard bounce failed", exc_info=True)
        audit(cfg.audit_log_path, op="delete", agent_id=agent_id, actor_ip=actor_ip,
              result="error", duration_ms=0, error=f"deferred bounce failed: {e}")


@router.delete("/{agent_id}", status_code=204)
async def delete(agent_id: str, request: Request, background_tasks: BackgroundTasks):
    denied = authz.admin_denied(request)
    if denied:
        return denied
    cfg = request.app.state.config
    actor_ip = request.client.host if request.client else "unknown"
    result = await delete_agent(agent_id)
    audit(cfg.audit_log_path, op="delete", agent_id=agent_id,
          actor_ip=actor_ip, result="ok" if result["ok"] else "error",
          duration_ms=0, error=result.get("error"))
    if not result["ok"]:
        raise HTTPException(status_code=404 if result.get("error") == "not_found" else 400,
                            detail=result.get("error"))
    if result.get("bounce_needed"):
        background_tasks.add_task(_bounce_after_delete, cfg, actor_ip, agent_id)
    return None


@router.post("/{agent_id}/identity")
async def set_identity(agent_id: str, body: SetIdentityRequest, request: Request) -> dict:
    denied = authz.admin_denied(request)
    if denied:
        return denied
    if not body.soulContent.strip():
        raise HTTPException(status_code=400, detail="soulContent must be non-empty")
    cfg = request.app.state.config
    actor_ip = request.client.host if request.client else "unknown"
    # Verify the agent exists (404 if unknown)
    entries = read_agents(cfg.hermes_stack_dir / ".env")
    entry = next((e for e in entries if e.id == agent_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="agent not found")
    # Optionally polish the soul content via the agent's gateway (falls back to template)
    gateway_key = request.app.state.hermes_gateway_key
    gateway_port = entry.gateway_port
    final_soul = (
        polish_persona(body.soulContent, gateway_port, gateway_key)
        if gateway_key
        else body.soulContent
    )
    # Write SOUL atomically
    soul_path = resolve_soul_path(agent_id, cfg.hermes_home, cfg.hermes_profiles_dir)
    write_soul(soul_path, final_soul)
    # Update displayName via existing rename path
    req = UpdateRequest(displayName=body.displayName, restart=False)
    await update_agent(agent_id, req)
    audit(cfg.audit_log_path, op="set_identity", agent_id=agent_id,
          actor_ip=actor_ip, result="ok", duration_ms=0)
    return await get_agent(agent_id, request)


@router.patch("/{agent_id}")
async def patch(agent_id: str, body: UpdateAgent, request: Request) -> dict:
    denied = authz.admin_denied(request)
    if denied:
        return denied
    cfg = request.app.state.config
    actor_ip = request.client.host if request.client else "unknown"
    req = UpdateRequest(**body.model_dump(exclude_unset=True))
    result = await update_agent(agent_id, req)
    audit(cfg.audit_log_path, op="update", agent_id=agent_id,
          actor_ip=actor_ip, result="ok" if result["ok"] else "error",
          duration_ms=0, error=result.get("error"))
    if not result["ok"]:
        raise HTTPException(status_code=404 if result.get("error") == "not_found" else 400,
                            detail=result.get("error"))
    return await get_agent(agent_id, request)


def _supabase_creds() -> "tuple[str, str] | None":
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    return (url, key) if url and key else None


@router.post("/{agent_id}/avatar")
async def upload_avatar(agent_id: str, request: Request) -> dict:
    denied = authz.admin_denied(request)
    if denied:
        return denied
    cfg = request.app.state.config
    entries = read_agents(cfg.hermes_stack_dir / ".env")
    if not any(e.id == agent_id for e in entries):
        raise HTTPException(status_code=404, detail="agent not found")
    creds = _supabase_creds()
    if not creds:
        raise HTTPException(status_code=503, detail="supabase not configured")
    sb_url, key = creds
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="empty body")
    # Instance-scoped path: the orchestrator is the only writer that knows its
    # INSTANCE_ID, so this is how the shared bucket stays tenant-isolated on the
    # one shared Supabase project. Service role bypasses storage RLS.
    path = f"shared/{cfg.instance_id}/{agent_id}.jpg"
    try:
        resp = httpx.post(
            f"{sb_url}/storage/v1/object/agent-avatars/{path}",
            content=body,
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Content-Type": "image/jpeg", "x-upsert": "true"},
            timeout=10.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"storage upload failed: {exc}")
    public = f"{sb_url}/storage/v1/object/public/agent-avatars/{path}?t={int(time.time() * 1000)}"
    return {"avatar_url": public}
