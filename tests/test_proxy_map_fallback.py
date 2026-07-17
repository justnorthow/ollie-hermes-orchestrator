"""UI-created agents must be routable immediately: the HERMES_*_URLS maps are
only re-rendered at provision time, so a freshly created agent is absent from
them until the next re-provision — /v1/runs/<id> then 503s and the dashboard
shows the agent OFFLINE (prod 'pam', 2026-07-17). The resolvers fall back to
AGENTS_JSON (which create_agent maintains synchronously), deriving loopback
URLs from the agent's ports."""
import json

from src.api.runs import _gateway_base
from src.api.sessions import _dashboard_base


def _seed_agents(stack, agents):
    (stack / ".env").write_text("AGENTS_JSON=" + json.dumps(agents) + "\n")


PAM = {
    "id": "pam", "name": "Pam B.",
    "gatewayUrl": "http://host.docker.internal:8644",
    "dashboardUrl": "http://host.docker.internal:9122",
}


def test_gateway_base_falls_back_to_agents_json(fake_env, monkeypatch):
    monkeypatch.setenv("HERMES_GATEWAY_URLS", json.dumps({"default": "http://127.0.0.1:8642"}))
    monkeypatch.delenv("HERMES_GATEWAY_URL", raising=False)
    _seed_agents(fake_env["stack"], [PAM])
    assert _gateway_base("pam") == "http://127.0.0.1:8644"


def test_gateway_map_still_wins_over_agents_json(fake_env, monkeypatch):
    """Operator-set map entries are overrides; the fallback only fills gaps."""
    monkeypatch.setenv("HERMES_GATEWAY_URLS", json.dumps({"pam": "http://127.0.0.1:9999"}))
    _seed_agents(fake_env["stack"], [PAM])
    assert _gateway_base("pam") == "http://127.0.0.1:9999"


def test_gateway_base_unknown_agent_keeps_legacy_fallback(fake_env, monkeypatch):
    monkeypatch.setenv("HERMES_GATEWAY_URLS", "{}")
    monkeypatch.setenv("HERMES_GATEWAY_URL", "http://127.0.0.1:8000")
    _seed_agents(fake_env["stack"], [])
    assert _gateway_base("ghost") == "http://127.0.0.1:8000"


def test_dashboard_base_falls_back_to_agents_json(fake_env, monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_URLS", json.dumps({"default": "http://127.0.0.1:9119"}))
    _seed_agents(fake_env["stack"], [PAM])
    assert _dashboard_base("pam") == "http://127.0.0.1:9122"


def test_dashboard_base_unknown_agent_returns_none(fake_env, monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_URLS", "{}")
    _seed_agents(fake_env["stack"], [])
    assert _dashboard_base("ghost") is None
