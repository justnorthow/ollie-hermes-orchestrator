"""Maintenance of the orchestrator's HERMES_GATEWAY_URLS / HERMES_DASHBOARD_URLS
proxy maps.

These map an agent id to its loopback gateway/dashboard URL. Historically they
were written ONLY by the install scripts at provision time
(detect_agents | scripts/lib/render-proxy-maps.py), so an agent created later
through the dashboard UI or ollie-fleetctl was absent from both and
check-box-config.sh reported the box as not done-done. Chat still worked, because
agents_json.loopback_url_for() derives the URL from AGENTS_JSON — see the prod
'pam' incident, 2026-07-17.

The rule here is deliberately add-if-missing rather than the install script's
regenerate-if-not-covering. Driven per-operation, a full re-render would leave a
deleted agent's entry behind (its map still "covers" every remaining id, so it is
kept) and would discard operator-pinned values on the regenerate branch. Removal
is therefore explicit, via drop_ids.
"""
import json
import logging
from pathlib import Path

from src.agents_json import AgentEntry, set_env_key

_logger = logging.getLogger(__name__)

GATEWAY_KEY = "HERMES_GATEWAY_URLS"
DASHBOARD_KEY = "HERMES_DASHBOARD_URLS"


def _read_map(env_path: Path, key: str) -> dict:
    """Parse one JSON object out of the .env, or {} if it is absent, unparseable
    or not an object — all of which mean 'nothing trustworthy here', and the
    caller then repopulates from AGENTS_JSON."""
    value = None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            value = line[len(key) + 1:]
    if value is None:
        return {}
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def sync(env_path: Path, agents: list[AgentEntry], *,
         drop_ids: tuple[str, ...] = ()) -> dict[str, list[str]]:
    """Make both proxy maps cover every agent, preserving operator entries.

    Existing entries are never overwritten, so an operator who pinned a custom
    URL keeps it. Entries are removed only when named in drop_ids. Returns
    {"added": [...], "dropped": [...]} for logging.
    """
    if not env_path.exists():
        # A box without an orchestrator .env is not one we should be creating
        # one for; the install scripts own that file's existence.
        _logger.warning("proxy map sync skipped: %s does not exist", env_path)
        return {"added": [], "dropped": []}

    added: list[str] = []
    dropped: list[str] = []
    for key, port_attr in ((GATEWAY_KEY, "gateway_port"),
                           (DASHBOARD_KEY, "dashboard_port")):
        current = _read_map(env_path, key)
        updated = dict(current)
        for agent_id in drop_ids:
            if updated.pop(agent_id, None) is not None:
                dropped.append(agent_id)
        for entry in agents:
            if entry.id in updated:
                continue
            updated[entry.id] = f"http://127.0.0.1:{getattr(entry, port_attr)}"
            added.append(entry.id)
        if updated != current:
            set_env_key(env_path, key, json.dumps(updated))
    return {"added": sorted(set(added)), "dropped": sorted(set(dropped))}
