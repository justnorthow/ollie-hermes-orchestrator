import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import authz
from src.api.folders import router
from src.auth import require_bearer
from src.config import Config


@pytest.fixture()
def cfg(tmp_path):
    return Config(
        orchestrator_key="test-key",
        hermes_stack_dir=tmp_path,
        hermes_home=tmp_path / ".hermes",
        hermes_profiles_dir=tmp_path / ".hermes" / "profiles",
        systemd_user_dir=tmp_path / ".config" / "systemd" / "user",
        audit_log_path=tmp_path / "audit.log",
        instance_id="default",
    )


@pytest.fixture()
def client(cfg):
    app = FastAPI()
    app.state.config = cfg
    app.dependency_overrides[require_bearer] = lambda: None
    app.include_router(router)
    return TestClient(app)


@pytest.fixture()
def authed_client(cfg):
    """Client that sends a real bearer token (no override) to test auth rejection."""
    app = FastAPI()
    app.state.config = cfg
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def test_get_folders_empty(client):
    r = client.get("/v1/folders")
    assert r.status_code == 200
    assert r.json() == {"folders": []}


def test_put_and_get_folders(client):
    payload = {
        "folders": [
            {"id": "a", "name": "Listings", "order": 0, "appIds": ["x"]}
        ]
    }
    r = client.put("/v1/folders", json=payload)
    assert r.status_code == 200
    assert r.json() == payload

    r2 = client.get("/v1/folders")
    assert r2.status_code == 200
    result = r2.json()
    assert len(result["folders"]) == 1
    assert result["folders"][0]["id"] == "a"
    assert result["folders"][0]["appIds"] == ["x"]


def test_member_cannot_put_folders(client, monkeypatch):
    monkeypatch.setattr(authz.roles, "resolve_tier", lambda instance_id, user_id: "member")
    payload = {"folders": [{"id": "a", "name": "Listings", "order": 0, "appIds": ["x"]}]}
    r = client.put("/v1/folders", json=payload, headers={"X-Auth-User-Id": "member-1"})
    assert r.status_code == 403


def test_get_folders_no_auth_returns_401(authed_client):
    r = authed_client.get("/v1/folders")
    assert r.status_code in (401, 403)
