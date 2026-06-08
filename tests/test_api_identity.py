"""Tests for POST /v1/agents/{id}/identity endpoint."""
import json
import pytest
from fastapi.testclient import TestClient


def _auth():
    return {"Authorization": "Bearer topsecret"}


@pytest.fixture
def client_with_default(fake_env):
    """Client with a pre-seeded 'default' agent in AGENTS_JSON."""
    # Seed the default agent into AGENTS_JSON
    default_entry = json.dumps([{
        "id": "default",
        "name": "Ollie",
        "gatewayUrl": "http://host.docker.internal:8900",
        "dashboardUrl": "http://host.docker.internal:9100",
        "color": "#6366f1",
        "model": "claude-sonnet-4-6",
    }])
    env_path = fake_env["stack"] / ".env"
    env_path.write_text(f"HERMES_GATEWAY_KEY=k\nAGENTS_JSON={default_entry}\n")

    # Seed the default SOUL.md with the marker so needsIdentity=True initially
    hermes_home = fake_env["hermes_home"]
    soul = hermes_home / "SOUL.md"
    soul.write_text("<!-- OLLIE-SOUL-DEFAULT -->\n# Ollie stub")

    from src.api.main import create_app
    app = create_app()
    return TestClient(app), fake_env


def test_set_identity_writes_soul_and_renames(client_with_default):
    client, fake_env = client_with_default
    body = {"displayName": "Billie", "soulContent": "You are Billie."}
    r = client.post("/v1/agents/default/identity", json=body, headers=_auth())
    assert r.status_code == 200
    data = r.json()
    assert data["displayName"] == "Billie"
    assert data["needsIdentity"] is False
    # SOUL written
    soul_path = fake_env["hermes_home"] / "SOUL.md"
    assert soul_path.read_text() == "You are Billie."


def test_set_identity_unknown_agent_404(client_with_default):
    client, _ = client_with_default
    r = client.post(
        "/v1/agents/nope/identity",
        json={"displayName": "X", "soulContent": "Y"},
        headers=_auth(),
    )
    assert r.status_code == 404


def test_set_identity_empty_soul_400(client_with_default):
    client, _ = client_with_default
    r = client.post(
        "/v1/agents/default/identity",
        json={"displayName": "X", "soulContent": ""},
        headers=_auth(),
    )
    assert r.status_code == 400


def test_set_identity_whitespace_only_soul_400(client_with_default):
    client, _ = client_with_default
    r = client.post(
        "/v1/agents/default/identity",
        json={"displayName": "X", "soulContent": "   \n  "},
        headers=_auth(),
    )
    assert r.status_code == 400


def test_needs_identity_true_before_set(client_with_default):
    """GET /v1/agents returns needsIdentity=True for the default agent with marker stub."""
    client, _ = client_with_default
    r = client.get("/v1/agents", headers=_auth())
    assert r.status_code == 200
    agents = r.json()["agents"]
    default = next((a for a in agents if a["id"] == "default"), None)
    assert default is not None
    assert default["needsIdentity"] is True


def test_needs_identity_false_after_set(client_with_default):
    """After POST identity, GET /v1/agents shows needsIdentity=False."""
    client, fake_env = client_with_default
    body = {"displayName": "Billie", "soulContent": "You are Billie."}
    client.post("/v1/agents/default/identity", json=body, headers=_auth())
    r = client.get("/v1/agents", headers=_auth())
    agents = r.json()["agents"]
    default = next((a for a in agents if a["id"] == "default"), None)
    assert default["needsIdentity"] is False
