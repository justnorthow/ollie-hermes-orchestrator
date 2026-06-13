import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from src.agents_json import read_agents
from src.auth import require_bearer
from src.config import Config
from src.models import CreateApp

_logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/agents",
    tags=["apps"],
    dependencies=[Depends(require_bearer)],
)


def _apps_path(agent_id: str, cfg: Config) -> Path:
    return cfg.hermes_profiles_dir / agent_id / "apps.json"


def _read_apps(agent_id: str, cfg: Config) -> list[dict]:
    path = _apps_path(agent_id, cfg)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        _logger.warning("apps.json unreadable for %s, returning empty list", agent_id)
        return []


def _write_apps(agent_id: str, cfg: Config, apps: list[dict]) -> None:
    path = _apps_path(agent_id, cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(apps, indent=2))


def _require_agent(agent_id: str, cfg: Config) -> None:
    entries = read_agents(cfg.hermes_stack_dir / ".env")
    if not any(e.id == agent_id for e in entries):
        raise HTTPException(status_code=404, detail="agent not found")


@router.get("/{agent_id}/apps")
async def list_apps(agent_id: str, request: Request) -> dict:
    cfg = request.app.state.config
    _require_agent(agent_id, cfg)
    return {"apps": _read_apps(agent_id, cfg)}


@router.post("/{agent_id}/apps", status_code=201)
async def register_app(agent_id: str, body: CreateApp, request: Request) -> dict:
    cfg = request.app.state.config
    _require_agent(agent_id, cfg)
    if not body.id.strip():
        raise HTTPException(status_code=400, detail="id must be non-empty")
    if not body.label.strip():
        raise HTTPException(status_code=400, detail="label must be non-empty")
    if not body.componentType.strip():
        raise HTTPException(status_code=400, detail="componentType must be non-empty")
    apps = _read_apps(agent_id, cfg)
    app_dict = {**body.model_dump(), "agentId": agent_id}
    # Upsert: replace existing entry with the same id
    apps = [a for a in apps if a["id"] != body.id]
    apps.append(app_dict)
    apps.sort(key=lambda a: a.get("order", 0))
    _write_apps(agent_id, cfg, apps)
    return app_dict


@router.delete("/{agent_id}/apps/{app_id}", status_code=204)
async def delete_app(agent_id: str, app_id: str, request: Request) -> None:
    cfg = request.app.state.config
    _require_agent(agent_id, cfg)
    apps = _read_apps(agent_id, cfg)
    filtered = [a for a in apps if a["id"] != app_id]
    if len(filtered) == len(apps):
        raise HTTPException(status_code=404, detail="app not found")
    _write_apps(agent_id, cfg, filtered)
    return None
