"""Task 2a.1: gate agent create/delete/reconfigure + apps mutations to account_admin+.

Mounts the agents + apps routers WITH app.state.config so admin_denied's
tier-check path is exercised (unlike test_api_agents.py / test_apps_api.py,
which mount without identity headers and hit the identity-less trust path).
"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import agents as agents_module
from src.api import apps as apps_module
from src.api import authz
from src.auth import require_bearer


@pytest.fixture()
def app(tmp_path):
    (tmp_path / ".env").write_text(
        'AGENTS_JSON=['
        '{"id":"default","name":"Default","gatewayUrl":"http://host.docker.internal:9100",'
        '"dashboardUrl":"http://host.docker.internal:9101","color":"#111111","scope":"user"},'
        '{"id":"pam","name":"PAM","gatewayUrl":"http://host.docker.internal:9110",'
        '"dashboardUrl":"http://host.docker.internal:9111","color":"#222222","scope":"company",'
        '"manager_visible":false}'
        ']'
    )
    pam_apps = tmp_path / ".hermes" / "profiles" / "pam"
    pam_apps.mkdir(parents=True)
    (pam_apps / "apps.json").write_text(
        '[{"id":"private-app","label":"Private","icon":"","description":"",'
        '"componentType":"ExternalWebApp","config":{"url":"https://private.example"}}]'
    )
    application = FastAPI()
    application.state.config = SimpleNamespace(
        instance_id="sandbox",
        hermes_stack_dir=tmp_path,
        hermes_profiles_dir=tmp_path / ".hermes" / "profiles",
        hermes_home=tmp_path / ".hermes",
    )
    application.dependency_overrides[require_bearer] = lambda: None
    application.include_router(agents_module.router)
    application.include_router(apps_module.router)
    return application


@pytest.fixture()
def client(app):
    return TestClient(app)


def _as_member(monkeypatch):
    monkeypatch.setattr(authz.roles, "resolve_tier", lambda instance_id, user_id: "member")


def _member_headers():
    return {"X-Auth-User-Id": "user-123"}


def test_member_cannot_create_agent(client, monkeypatch):
    _as_member(monkeypatch)
    body = {
        "name": "paige", "provider": "anthropic", "model": "claude-sonnet-4.6",
        "apiKey": "sk-x", "displayName": "Paige", "color": "#aabbcc",
        "enabledSkills": [],
    }
    r = client.post("/v1/agents", json=body, headers=_member_headers())
    assert r.status_code == 403


def test_member_cannot_delete_agent(client, monkeypatch):
    _as_member(monkeypatch)
    r = client.delete("/v1/agents/x", headers=_member_headers())
    assert r.status_code == 403


def test_member_cannot_patch_agent(client, monkeypatch):
    _as_member(monkeypatch)
    r = client.patch("/v1/agents/x", json={"displayName": "New"}, headers=_member_headers())
    assert r.status_code == 403


def test_member_cannot_set_identity(client, monkeypatch):
    _as_member(monkeypatch)
    r = client.post(
        "/v1/agents/x/identity",
        json={"displayName": "X", "soulContent": "You are X."},
        headers=_member_headers(),
    )
    assert r.status_code == 403


def test_member_cannot_register_app(client, monkeypatch):
    _as_member(monkeypatch)
    payload = {
        "id": "dashboard", "label": "Dashboard", "icon": "", "description": "",
        "componentType": "Dashboard",
    }
    r = client.post("/v1/agents/x/apps", json=payload, headers=_member_headers())
    assert r.status_code == 403


def test_member_cannot_delete_app(client, monkeypatch):
    _as_member(monkeypatch)
    r = client.delete("/v1/agents/x/apps/y", headers=_member_headers())
    assert r.status_code == 403


def test_admin_denied_allows_account_admin(monkeypatch):
    class _StubRequest:
        def __init__(self, user_id, app):
            self.headers = {"X-Auth-User-Id": user_id} if user_id else {}
            self._app = app

        @property
        def app(self):
            if self._app is None:
                raise KeyError("app")
            return self._app

    cfg = SimpleNamespace(instance_id="sandbox", hermes_stack_dir=None)
    stub_app = SimpleNamespace(state=SimpleNamespace(config=cfg))

    monkeypatch.setattr(authz.roles, "resolve_tier", lambda instance_id, user_id: "account_admin")
    assert authz.admin_denied(_StubRequest("admin-1", stub_app)) is None

    monkeypatch.setattr(authz.roles, "resolve_tier", lambda instance_id, user_id: "member")
    denied = authz.admin_denied(_StubRequest("member-1", stub_app))
    assert denied is not None
    assert denied.status_code == 403

    # Identity-less caller (no X-Auth-User-Id) -> trusted internal -> None
    assert authz.admin_denied(_StubRequest("", stub_app)) is None


def test_reads_stay_open_for_member(client, monkeypatch):
    _as_member(monkeypatch)
    r = client.get("/v1/agents", headers=_member_headers())
    assert r.status_code == 200


def test_member_agent_list_only_contains_reachable_agents(client, monkeypatch):
    _as_member(monkeypatch)
    r = client.get("/v1/agents", headers=_member_headers())
    assert r.status_code == 200
    assert [agent["id"] for agent in r.json()["agents"]] == ["default"]


def test_member_cannot_get_unreachable_agent(client, monkeypatch):
    _as_member(monkeypatch)
    r = client.get("/v1/agents/pam", headers=_member_headers())
    assert r.status_code == 403


def test_member_cannot_list_unreachable_agent_apps(client, monkeypatch):
    _as_member(monkeypatch)
    r = client.get("/v1/agents/pam/apps", headers=_member_headers())
    assert r.status_code == 403
