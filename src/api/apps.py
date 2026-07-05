import json
import logging
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from src.agents_json import read_agents
from src.api import authz
from src.auth import require_bearer
from src.config import Config
from src.lock import file_lock
from src.models import CreateApp

_logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/agents",
    tags=["apps"],
    dependencies=[Depends(require_bearer)],
)

_APPS_LOCK = "apps.lock"


def _apps_path(agent_id: str, cfg: Config) -> Path:
    return cfg.hermes_profiles_dir / agent_id / "apps.json"


def _read_apps(agent_id: str, cfg: Config) -> list[dict]:
    path = _apps_path(agent_id, cfg)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # Guard against corrupt/unexpected non-list content (e.g. `{}`)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        _logger.warning("apps.json unreadable for %s, returning empty list", agent_id)
        return []


def _write_apps(agent_id: str, cfg: Config, apps: list[dict]) -> None:
    path = _apps_path(agent_id, cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / _APPS_LOCK
    text = json.dumps(apps, indent=2)
    with file_lock(lock_path):
        fd, tmp = tempfile.mkstemp(prefix=".apps.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def _require_agent(agent_id: str, cfg: Config) -> None:
    entries = read_agents(cfg.hermes_stack_dir / ".env")
    if not any(e.id == agent_id for e in entries):
        raise HTTPException(status_code=404, detail="agent not found")


@router.get("/{agent_id}/apps")
async def list_apps(agent_id: str, request: Request) -> dict:
    cfg = request.app.state.config
    _require_agent(agent_id, cfg)
    denied = authz.check_agent_access(request, agent_id, cfg)
    if denied:
        return denied
    return {"apps": _read_apps(agent_id, cfg)}


@router.post("/{agent_id}/apps", status_code=201)
async def register_app(agent_id: str, body: CreateApp, request: Request) -> dict:
    denied = authz.admin_denied(request)
    if denied:
        return denied
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
    denied = authz.admin_denied(request)
    if denied:
        return denied
    cfg = request.app.state.config
    _require_agent(agent_id, cfg)
    apps = _read_apps(agent_id, cfg)
    filtered = [a for a in apps if a["id"] != app_id]
    if len(filtered) == len(apps):
        raise HTTPException(status_code=404, detail="app not found")
    _write_apps(agent_id, cfg, filtered)
    return None
