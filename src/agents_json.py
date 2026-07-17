import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_AGENTS_LINE = re.compile(r"^AGENTS_JSON=(.*)$", re.MULTILINE)
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentEntry:
    id: str
    name: str
    gateway_port: int
    dashboard_port: int
    color: str
    model: Optional[str] = None
    subtitle: Optional[str] = None
    avatar_url: Optional[str] = None
    scope: str = "company"
    manager_visible: bool = False


def _gateway_url(port: int) -> str:
    return f"http://host.docker.internal:{port}"


def _dashboard_url(port: int) -> str:
    return f"http://host.docker.internal:{port}"


def _entry_to_json(e: AgentEntry) -> dict:
    d = {
        "id": e.id,
        "name": e.name,
        "gatewayUrl": _gateway_url(e.gateway_port),
        "dashboardUrl": _dashboard_url(e.dashboard_port),
        "color": e.color,
    }
    if e.model:
        d["model"] = e.model
    if e.subtitle:
        d["subtitle"] = e.subtitle
    if e.avatar_url:
        d["avatar_url"] = e.avatar_url
    d["scope"] = e.scope
    d["manager_visible"] = e.manager_visible
    return d


def _json_to_entry(d: dict) -> AgentEntry:
    return AgentEntry(
        id=d["id"],
        name=d.get("name", d["id"]),
        gateway_port=int(d["gatewayUrl"].rsplit(":", 1)[1]),
        dashboard_port=int(d["dashboardUrl"].rsplit(":", 1)[1]),
        color=d.get("color", "#888888"),
        model=d.get("model"),
        subtitle=d.get("subtitle"),
        avatar_url=d.get("avatar_url"),
        scope=d.get("scope", "company"),
        manager_visible=bool(d.get("manager_visible", False)),
    )


def read_agents(env_path: Path) -> list[AgentEntry]:
    text = env_path.read_text(encoding="utf-8")
    m = _AGENTS_LINE.search(text)
    if not m:
        return []
    return [_json_to_entry(d) for d in json.loads(m.group(1))]


def _write_env_atomic(env_path: Path, new_text: str) -> None:
    dir_ = env_path.parent
    fd, tmp = tempfile.mkstemp(prefix=".env.", dir=str(dir_))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_text)
        os.replace(tmp, env_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _replace_agents_line(env_text: str, entries: list[AgentEntry]) -> str:
    new_value = json.dumps([_entry_to_json(e) for e in entries], separators=(",", ":"))
    new_line = f"AGENTS_JSON={new_value}"
    if _AGENTS_LINE.search(env_text):
        # Pass a replacement FUNCTION, not the string: re.sub interprets
        # backslash escapes (\uXXXX from non-ASCII names, \\ from literal
        # backslashes) in a string replacement, raising "bad escape \u" or
        # corrupting the value. A function return is used verbatim.
        return _AGENTS_LINE.sub(lambda _m: new_line, env_text)
    sep = "" if env_text.endswith("\n") or not env_text else "\n"
    return f"{env_text}{sep}{new_line}\n"


def write_agent(env_path: Path, entry: AgentEntry) -> None:
    text = env_path.read_text(encoding="utf-8")
    entries = read_agents(env_path)
    entries = [e for e in entries if e.id != entry.id]
    entries.append(entry)
    _write_env_atomic(env_path, _replace_agents_line(text, entries))


def remove_agent(env_path: Path, agent_id: str) -> None:
    text = env_path.read_text(encoding="utf-8")
    entries = [e for e in read_agents(env_path) if e.id != agent_id]
    _write_env_atomic(env_path, _replace_agents_line(text, entries))


def set_env_key(env_path: Path, key: str, value: str) -> None:
    """Atomically upsert one KEY=value line in a stack .env. Replaces the
    first occurrence in place, drops any duplicates, appends when absent.
    Replacement uses a function (not a string) for the same backslash-escape
    reason as _replace_agents_line."""
    if "\n" in value or "\r" in value:
        raise ValueError(f"{key} must be a single-line value")
    text = env_path.read_text(encoding="utf-8")
    line = f"{key}={value}"
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    if pattern.search(text):
        first_done = [False]

        def _sub(m):
            if not first_done[0]:
                first_done[0] = True
                return line
            return "\x00DROP\x00"

        text = pattern.sub(_sub, text)
        text = "\n".join(l for l in text.split("\n") if l != "\x00DROP\x00")
    else:
        sep = "" if text.endswith("\n") or not text else "\n"
        text = f"{text}{sep}{line}\n"
    _write_env_atomic(env_path, text)


def loopback_url_for(agent_id: str, kind: str) -> "str | None":
    """Loopback URL for an agent's gateway ('gateway') or dashboard
    ('dashboard'), derived from the stack .env's AGENTS_JSON ports.

    Fallback for the HERMES_GATEWAY_URLS / HERMES_DASHBOARD_URLS proxy maps:
    those are only re-rendered by the install scripts at provision time, so a
    UI-created agent is missing from them until the next re-provision — its
    /v1/runs probes 503 and the dashboard shows it OFFLINE (prod 'pam',
    2026-07-17). AGENTS_JSON, by contrast, is maintained synchronously by
    create/delete, so it is always current. The orchestrator runs host-native,
    hence 127.0.0.1 rather than AGENTS_JSON's host.docker.internal URLs."""
    from src.config import Config  # local import: Config has no dependency back here, but keep the module import-light

    try:
        env_path = Config.load().hermes_stack_dir / ".env"
        for e in read_agents(env_path):
            if e.id == agent_id:
                port = e.gateway_port if kind == "gateway" else e.dashboard_port
                return f"http://127.0.0.1:{port}"
    except Exception:
        _logger.warning("AGENTS_JSON fallback resolution failed for %s", agent_id, exc_info=True)
    return None
