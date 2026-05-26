import logging
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from src.agents_json import read_agents
from src.auth import require_bearer
from src.audit import audit
from src.config import Config
from src.lifecycle import CreateRequest, UpdateRequest, create_agent, delete_agent, update_agent
from src.models import Agent, CreateAgent, UpdateAgent
from src.sse import sse_event

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/agents", tags=["agents"], dependencies=[Depends(require_bearer)])


def _entry_to_agent(e) -> dict:
    return Agent(
        id=e.id, displayName=e.name, color=e.color,
        provider="anthropic", model=e.model or "unknown",
        gatewayPort=e.gateway_port, dashboardPort=e.dashboard_port,
    ).model_dump()


@router.get("")
async def list_agents(request: Request) -> dict:
    cfg = request.app.state.config
    entries = read_agents(cfg.hermes_stack_dir / ".env")
    return {"agents": [_entry_to_agent(e) for e in entries]}


@router.get("/{agent_id}")
async def get_agent(agent_id: str, request: Request) -> dict:
    cfg = request.app.state.config
    entries = read_agents(cfg.hermes_stack_dir / ".env")
    e = next((x for x in entries if x.id == agent_id), None)
    if not e:
        raise HTTPException(status_code=404, detail="not_found")
    return _entry_to_agent(e)


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create(body: CreateAgent, request: Request) -> StreamingResponse:
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


@router.delete("/{agent_id}", status_code=204)
async def delete(agent_id: str, request: Request):
    cfg = request.app.state.config
    actor_ip = request.client.host if request.client else "unknown"
    result = await delete_agent(agent_id)
    audit(cfg.audit_log_path, op="delete", agent_id=agent_id,
          actor_ip=actor_ip, result="ok" if result["ok"] else "error",
          duration_ms=0, error=result.get("error"))
    if not result["ok"]:
        raise HTTPException(status_code=404 if result.get("error") == "not_found" else 400,
                            detail=result.get("error"))
    return None


@router.patch("/{agent_id}")
async def patch(agent_id: str, body: UpdateAgent, request: Request) -> dict:
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
