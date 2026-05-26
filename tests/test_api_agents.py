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
