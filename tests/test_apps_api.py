import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pathlib import Path

from src.api.apps import router
from src.auth import require_bearer
from src.config import Config


@pytest.fixture()
def cfg(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        'AGENTS_JSON=[{"id":"marketing-expert","name":"Marketing Expert",'
        '"gatewayUrl":"http://host.docker.internal:9110",'
        '"dashboardUrl":"http://host.docker.internal:9111","color":"#7c3aed"}]'
    )
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


def test_list_apps_empty(client):
    r = client.get("/v1/agents/marketing-expert/apps")
    assert r.status_code == 200
    assert r.json() == {"apps": []}


def test_register_and_list_app(client):
    payload = {
        "id": "marketing-dashboard",
        "label": "Marketing Dashboard",
        "icon": "<path d='M3 3h18v18H3z'/>",
        "description": "Live metrics",
        "componentType": "MarketingDashboard",
    }
    r = client.post("/v1/agents/marketing-expert/apps", json=payload)
    assert r.status_code == 201
    created = r.json()
    assert created["id"] == "marketing-dashboard"
    assert created["agentId"] == "marketing-expert"

    r2 = client.get("/v1/agents/marketing-expert/apps")
    assert len(r2.json()["apps"]) == 1


def test_register_app_is_idempotent(client):
    payload = {
        "id": "marketing-dashboard",
        "label": "Marketing Dashboard",
        "icon": "",
        "description": "",
        "componentType": "MarketingDashboard",
    }
    client.post("/v1/agents/marketing-expert/apps", json=payload)
    client.post("/v1/agents/marketing-expert/apps", json=payload)
    r = client.get("/v1/agents/marketing-expert/apps")
    assert len(r.json()["apps"]) == 1


def test_apps_sorted_by_order(client):
    for i, app_id in enumerate(["c", "a", "b"]):
        client.post("/v1/agents/marketing-expert/apps", json={
            "id": app_id, "label": app_id, "icon": "", "description": "",
            "componentType": "X", "order": 2 - i,
        })
    apps = client.get("/v1/agents/marketing-expert/apps").json()["apps"]
    assert [a["id"] for a in apps] == ["b", "a", "c"]


def test_delete_app(client):
    client.post("/v1/agents/marketing-expert/apps", json={
        "id": "marketing-dashboard", "label": "Marketing Dashboard",
        "icon": "", "description": "", "componentType": "MarketingDashboard",
    })
    r = client.delete("/v1/agents/marketing-expert/apps/marketing-dashboard")
    assert r.status_code == 204
    assert client.get("/v1/agents/marketing-expert/apps").json()["apps"] == []


def test_delete_nonexistent_returns_404(client):
    r = client.delete("/v1/agents/marketing-expert/apps/nonexistent")
    assert r.status_code == 404


def test_unknown_agent_returns_404(client):
    r = client.get("/v1/agents/no-such-agent/apps")
    assert r.status_code == 404


def test_register_empty_id_returns_400(client):
    r = client.post("/v1/agents/marketing-expert/apps", json={
        "id": "", "label": "Dashboard", "icon": "", "description": "", "componentType": "X",
    })
    assert r.status_code == 400


def test_register_empty_label_returns_400(client):
    r = client.post("/v1/agents/marketing-expert/apps", json={
        "id": "my-app", "label": "", "icon": "", "description": "", "componentType": "X",
    })
    assert r.status_code == 400


def test_register_empty_component_type_returns_400(client):
    r = client.post("/v1/agents/marketing-expert/apps", json={
        "id": "my-app", "label": "My App", "icon": "", "description": "", "componentType": "",
    })
    assert r.status_code == 400
