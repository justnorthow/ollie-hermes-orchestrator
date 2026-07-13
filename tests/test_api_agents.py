import json
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(fake_env):
    from src.api.main import create_app
    app = create_app()
    return TestClient(app)


def _auth():
    return {"Authorization": "Bearer topsecret"}


def test_list_agents_starts_empty(client):
    r = client.get("/v1/agents", headers=_auth())
    assert r.status_code == 200
    assert r.json() == {"agents": []}


def test_create_then_list(client):
    body = {
        "name": "paige", "provider": "anthropic", "model": "claude-sonnet-4.6",
        "apiKey": "sk-x", "displayName": "Paige", "color": "#aabbcc",
        "enabledSkills": [],
    }
    r = client.post("/v1/agents", json=body, headers=_auth())
    assert r.status_code == 202
    events = []
    for raw in r.iter_lines():
        if isinstance(raw, bytes):
            raw = raw.decode()
        if raw.startswith("data: "):
            events.append(json.loads(raw[6:]))
    assert any(ev.get("event") == "done" for ev in events)
    r2 = client.get("/v1/agents", headers=_auth())
    assert r2.status_code == 200
    ids = [a["id"] for a in r2.json()["agents"]]
    assert "paige" in ids


def test_delete_round_trips(client):
    body = {"name": "tmp", "provider": "anthropic", "model": "m",
            "apiKey": "k", "enabledSkills": []}
    r = client.post("/v1/agents", json=body, headers=_auth())
    list(r.iter_lines())  # drain
    r2 = client.delete("/v1/agents/tmp", headers=_auth())
    assert r2.status_code == 204
    r3 = client.get("/v1/agents/tmp", headers=_auth())
    assert r3.status_code == 404


def test_unauthenticated_returns_401(client):
    assert client.get("/v1/agents").status_code == 401


def _seed_agents_json(stack, entries):
    (stack / ".env").write_text(
        "HERMES_GATEWAY_KEY=k\n"
        f"AGENTS_JSON={json.dumps(entries, separators=(',', ':'))}\n"
    )


def test_list_prefers_live_profile_model_over_agents_json(client, fake_env):
    # AGENTS_JSON's model is a cache written only by orchestrator create/update;
    # `hermes model set` bypasses it. The API must serve the live config value.
    _seed_agents_json(fake_env["stack"], [{
        "id": "marketing-agent", "name": "Olivia",
        "gatewayUrl": "http://host.docker.internal:8643",
        "dashboardUrl": "http://host.docker.internal:9121",
        "color": "#7c3aed", "model": "gpt-5.5",
    }])
    profile = fake_env["profiles"] / "marketing-agent"
    profile.mkdir()
    (profile / "config.yaml").write_text(
        "model:\n  default: gpt-5.6-sol\n  provider: openai-codex\n"
    )
    r = client.get("/v1/agents", headers=_auth())
    assert r.status_code == 200
    agents = {a["id"]: a for a in r.json()["agents"]}
    assert agents["marketing-agent"]["model"] == "gpt-5.6-sol"


def test_default_agent_model_read_from_global_config(client, fake_env):
    # The default profile has no AGENTS_JSON model entry at all; its model
    # lives in ~/.hermes/config.yaml (fixture sets gpt-5.5).
    _seed_agents_json(fake_env["stack"], [{
        "id": "default", "name": "Ollie",
        "gatewayUrl": "http://host.docker.internal:8642",
        "dashboardUrl": "http://host.docker.internal:9119",
        "color": "#888888",
    }])
    r = client.get("/v1/agents", headers=_auth())
    assert r.status_code == 200
    agents = {a["id"]: a for a in r.json()["agents"]}
    assert agents["default"]["model"] == "gpt-5.5"


def test_model_falls_back_to_agents_json_when_no_profile_config(client, fake_env):
    _seed_agents_json(fake_env["stack"], [{
        "id": "ghost", "name": "Ghost",
        "gatewayUrl": "http://host.docker.internal:8650",
        "dashboardUrl": "http://host.docker.internal:9150",
        "color": "#123456", "model": "cached-model",
    }])
    r = client.get("/v1/agents", headers=_auth())
    assert r.status_code == 200
    agents = {a["id"]: a for a in r.json()["agents"]}
    assert agents["ghost"]["model"] == "cached-model"
