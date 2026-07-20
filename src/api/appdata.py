"""Generic per-agent app-data API.

Serves and mutates structured JSON files that agent skills write to
<profiles>/<agent>/workspace/appdata/. Deliberately agent-agnostic: nothing
prospecting-specific lives here. See spec 2026-07-20-agent-app-uis-design.md.
"""
import json
import os
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from src.agents_json import read_agents
from src.api import authz
from src.auth import require_bearer
from src.config import Config
from src.lock import file_lock

router = APIRouter(
    prefix="/v1/agents",
    tags=["appdata"],
    dependencies=[Depends(require_bearer)],
)

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9/-]*$")


def _require_agent(agent_id: str, cfg: Config) -> None:
    entries = read_agents(cfg.hermes_stack_dir / ".env")
    if not any(e.id == agent_id for e in entries):
        raise HTTPException(status_code=404, detail="agent not found")


def _appdata_dir(agent_id: str, cfg: Config) -> Path:
    return cfg.hermes_profiles_dir / agent_id / "workspace" / "appdata"


def _resolve_key(agent_id: str, key: str, cfg: Config) -> Path:
    if ".." in key or not _KEY_RE.match(key):
        raise HTTPException(status_code=400, detail="invalid key")
    base = _appdata_dir(agent_id, cfg).resolve()
    path = (base / f"{key}.json").resolve()
    # Belt-and-braces: the regex already blocks traversal, but never trust it alone.
    if base != path and base not in path.parents:
        raise HTTPException(status_code=400, detail="invalid key")
    return path


class PointerUpdate(BaseModel):
    pointer: str
    value: object = None


class PatchAppData(BaseModel):
    baseVersion: int
    updates: list[PointerUpdate] = Field(min_length=1)


def _apply_pointer(doc: dict, pointer: str, value: object) -> None:
    """Set `value` at RFC 6901 `pointer` inside `doc`. Raises HTTPException(400) on
    any invalid pointer (bad syntax, missing parent, list index out of range)."""
    if not pointer.startswith("/"):
        raise HTTPException(status_code=400, detail=f"invalid pointer {pointer}")
    parts = [p.replace("~1", "/").replace("~0", "~") for p in pointer[1:].split("/")]
    target: object = doc
    try:
        for part in parts[:-1]:
            target = target[int(part)] if isinstance(target, list) else target[part]
        last = parts[-1]
        if isinstance(target, list):
            target[int(last)] = value  # index must exist — no appends via PATCH
        elif isinstance(target, dict):
            target[last] = value
        else:
            raise KeyError(last)
    except (KeyError, IndexError, ValueError, TypeError):
        raise HTTPException(status_code=400, detail=f"invalid pointer {pointer}")


@router.get("/{agent_id}/appdata/{key:path}")
async def get_appdata(agent_id: str, key: str, request: Request) -> dict:
    cfg = request.app.state.config
    _require_agent(agent_id, cfg)
    denied = authz.check_agent_access(request, agent_id, cfg)
    if denied:
        return denied
    path = _resolve_key(agent_id, key, cfg)
    if not path.exists():
        raise HTTPException(status_code=404, detail="appdata not found")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raise HTTPException(status_code=500, detail="appdata file unreadable")


@router.patch("/{agent_id}/appdata/{key:path}")
async def patch_appdata(agent_id: str, key: str, body: PatchAppData, request: Request) -> dict:
    cfg = request.app.state.config
    _require_agent(agent_id, cfg)
    denied = authz.check_agent_access(request, agent_id, cfg)
    if denied:
        return denied
    path = _resolve_key(agent_id, key, cfg)
    if not path.exists():
        raise HTTPException(status_code=404, detail="appdata not found")
    lock_path = path.parent / f".{path.stem}.lock"
    with file_lock(lock_path):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raise HTTPException(status_code=500, detail="appdata file unreadable")
        if doc.get("version") != body.baseVersion:
            raise HTTPException(status_code=409, detail="version conflict")
        for u in body.updates:
            _apply_pointer(doc, u.pointer, u.value)
        doc["version"] = body.baseVersion + 1
        text = json.dumps(doc, indent=2)
        fd, tmp = tempfile.mkstemp(prefix=".appdata.", dir=str(path.parent))
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
    return {"version": doc["version"]}
