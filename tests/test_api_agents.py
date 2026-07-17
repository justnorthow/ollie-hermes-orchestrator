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


def _create_tmp_agent(client):
    body = {"name": "tmp", "provider": "anthropic", "model": "m",
            "apiKey": "k", "enabledSkills": []}
    r = client.post("/v1/agents", json=body, headers=_auth())
    list(r.iter_lines())  # drain the SSE stream so the create completes


def test_delete_schedules_deferred_dashboard_bounce(client, monkeypatch):
    """The bounce runs as a BackgroundTask after the 204 is sent (mirrors
    instance.py's deferred _bounce_after_write) — TestClient executes
    background tasks as part of the request cycle, so a scheduled bounce is
    observable as exactly one call alongside the 204."""
    import src.api.agents as agents_mod
    calls: list[str] = []
    monkeypatch.setattr(agents_mod, "bounce_dashboard", lambda: calls.append("bounce"))
    _create_tmp_agent(client)
    r = client.delete("/v1/agents/tmp", headers=_auth())
    assert r.status_code == 204
    assert calls == ["bounce"]


def test_delete_already_gone_does_not_bounce(client, monkeypatch):
    import src.api.agents as agents_mod
    calls: list[str] = []
    monkeypatch.setattr(agents_mod, "bounce_dashboard", lambda: calls.append("bounce"))
    r = client.delete("/v1/agents/never-existed", headers=_auth())
    assert r.status_code == 204
    assert calls == []


def test_delete_bounce_failure_does_not_break_the_204(client, monkeypatch):
    """A failing deferred bounce must never surface to the caller — the delete
    itself succeeded; the operator can re-bounce manually."""
    import src.api.agents as agents_mod

    def boom():
        raise RuntimeError("docker down")

    monkeypatch.setattr(agents_mod, "bounce_dashboard", boom)
    _create_tmp_agent(client)
    r = client.delete("/v1/agents/tmp", headers=_auth())
    assert r.status_code == 204


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


def test_list_agents_includes_subtitle(client, fake_env):
    _seed_agents_json(fake_env["stack"], [{
        "id": "olivia", "name": "Olivia",
        "gatewayUrl": "http://host.docker.internal:8643",
        "dashboardUrl": "http://host.docker.internal:9121",
        "color": "#7c3aed", "model": "gpt-5.5",
        "subtitle": "AI Head of Marketing",
    }])
    resp = client.get("/v1/agents", headers=_auth())
    assert resp.status_code == 200
    agent = next(a for a in resp.json()["agents"] if a["id"] == "olivia")
    assert agent["subtitle"] == "AI Head of Marketing"


def test_list_agents_subtitle_null_when_unset(client, fake_env):
    _seed_agents_json(fake_env["stack"], [{
        "id": "olivia", "name": "Olivia",
        "gatewayUrl": "http://host.docker.internal:8643",
        "dashboardUrl": "http://host.docker.internal:9121",
        "color": "#7c3aed", "model": "gpt-5.5",
    }])
    resp = client.get("/v1/agents", headers=_auth())
    assert resp.status_code == 200
    agent = next(a for a in resp.json()["agents"] if a["id"] == "olivia")
    assert agent["subtitle"] is None


def test_update_subtitle_persists(client, fake_env):
    _seed_agents_json(fake_env["stack"], [{
        "id": "olivia", "name": "Olivia",
        "gatewayUrl": "http://host.docker.internal:8643",
        "dashboardUrl": "http://host.docker.internal:9121",
        "color": "#7c3aed", "model": "gpt-5.5",
    }])
    r = client.patch("/v1/agents/olivia", json={"subtitle": "Chief of Staff"}, headers=_auth())
    assert r.status_code == 200
    assert r.json()["subtitle"] == "Chief of Staff"
    from src.agents_json import read_agents
    entries = read_agents(fake_env["stack"] / ".env")
    entry = next(e for e in entries if e.id == "olivia")
    assert entry.subtitle == "Chief of Staff"


def test_update_subtitle_empty_string_clears(client, fake_env):
    _seed_agents_json(fake_env["stack"], [{
        "id": "olivia", "name": "Olivia",
        "gatewayUrl": "http://host.docker.internal:8643",
        "dashboardUrl": "http://host.docker.internal:9121",
        "color": "#7c3aed", "model": "gpt-5.5",
        "subtitle": "AI Head of Marketing",
    }])
    r = client.patch("/v1/agents/olivia", json={"subtitle": ""}, headers=_auth())
    assert r.status_code == 200
    assert r.json()["subtitle"] is None
    from src.agents_json import read_agents
    entries = read_agents(fake_env["stack"] / ".env")
    entry = next(e for e in entries if e.id == "olivia")
    assert entry.subtitle is None
    r2 = client.get("/v1/agents", headers=_auth())
    agent = next(a for a in r2.json()["agents"] if a["id"] == "olivia")
    assert agent["subtitle"] is None


def test_subtitle_too_long_rejected(client, fake_env):
    _seed_agents_json(fake_env["stack"], [{
        "id": "olivia", "name": "Olivia",
        "gatewayUrl": "http://host.docker.internal:8643",
        "dashboardUrl": "http://host.docker.internal:9121",
        "color": "#7c3aed", "model": "gpt-5.5",
    }])
    r = client.patch("/v1/agents/olivia", json={"subtitle": "x" * 65}, headers=_auth())
    assert r.status_code == 422


def test_update_other_field_does_not_wipe_subtitle(client, fake_env):
    # Task 1 review carry-over: update_agent's entry-rebuild must forward
    # entry.subtitle when the request doesn't touch it, or ANY update
    # silently wipes an existing subtitle.
    _seed_agents_json(fake_env["stack"], [{
        "id": "olivia", "name": "Olivia",
        "gatewayUrl": "http://host.docker.internal:8643",
        "dashboardUrl": "http://host.docker.internal:9121",
        "color": "#7c3aed", "model": "gpt-5.5",
        "subtitle": "AI Head of Marketing",
    }])
    r = client.patch("/v1/agents/olivia", json={"color": "#111111"}, headers=_auth())
    assert r.status_code == 200
    assert r.json()["subtitle"] == "AI Head of Marketing"
    from src.agents_json import read_agents
    entries = read_agents(fake_env["stack"] / ".env")
    entry = next(e for e in entries if e.id == "olivia")
    assert entry.subtitle == "AI Head of Marketing"


def test_patch_sets_and_clears_avatar_url(client, fake_env):
    _seed_agents_json(fake_env["stack"], [{
        "id": "olivia", "name": "Olivia",
        "gatewayUrl": "http://host.docker.internal:8643",
        "dashboardUrl": "http://host.docker.internal:9121",
        "color": "#7c3aed", "model": "gpt-5.5",
    }])
    # set
    r = client.patch("/v1/agents/olivia", json={"avatar_url": "https://x/shared/olivia.jpg?t=1"},
                      headers=_auth())
    assert r.status_code == 200
    assert r.json()["avatar_url"] == "https://x/shared/olivia.jpg?t=1"
    # list surfaces it
    r2 = client.get("/v1/agents", headers=_auth())
    agent = next(a for a in r2.json()["agents"] if a["id"] == "olivia")
    assert agent["avatar_url"] == "https://x/shared/olivia.jpg?t=1"
    # clear with ""
    r = client.patch("/v1/agents/olivia", json={"avatar_url": ""}, headers=_auth())
    assert r.status_code == 200
    assert r.json()["avatar_url"] is None
    from src.agents_json import read_agents
    entries = read_agents(fake_env["stack"] / ".env")
    entry = next(e for e in entries if e.id == "olivia")
    assert entry.avatar_url is None
