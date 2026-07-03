"""whoami + admin API (Phase 2a). Supabase + role store monkeypatched."""
import json
import types
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.admin as admin
import src.api.roles as roles
import src.api.authz as authz
from src.api.admin import router as admin_router
from src.auth import require_bearer

ADMIN = "aaaaaaaa-0000-0000-0000-00000000000a"
MEMBER = "mmmmmmmm-0000-0000-0000-00000000000m"


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.state.config = types.SimpleNamespace(instance_id="sandbox", hermes_stack_dir=None)
    app.include_router(admin_router)
    app.dependency_overrides[require_bearer] = lambda: None
    monkeypatch.setattr(authz, "reachable_agent_ids", lambda request, cfg: ["default"])
    return TestClient(app)


def test_whoami_requires_identity(client):
    assert client.get("/v1/whoami").status_code == 401


def test_whoami_returns_tier_and_reachable(client, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "member")
    monkeypatch.setattr(roles, "get_labels", lambda i: dict(roles.DEFAULT_LABELS))
    r = client.get("/v1/whoami", headers={"X-Auth-User-Id": MEMBER})
    assert r.status_code == 200
    assert r.json() == {"userId": MEMBER, "tier": "member",
                        "label": "Member", "reachableAgentIds": ["default"]}


def test_admin_users_requires_admin(client, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "member")
    assert client.get("/v1/admin/users", headers={"X-Auth-User-Id": MEMBER}).status_code == 403


def test_admin_set_role_writes_and_audits(client, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "account_admin")
    writes, events = [], []
    monkeypatch.setattr(roles, "set_tier",
                        lambda inst, uid, tier, by: writes.append((inst, uid, tier, by)))
    monkeypatch.setattr(admin, "_emit_admin_event", lambda *a, **k: events.append(a))
    r = client.put(f"/v1/admin/users/{MEMBER}/role", json={"tier": "manager"},
                   headers={"X-Auth-User-Id": ADMIN})
    assert r.status_code == 200
    assert writes == [("sandbox", MEMBER, "manager", ADMIN)]
    assert len(events) == 1


def test_account_admin_cannot_assign_platform_operator(client, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "account_admin")
    r = client.put(f"/v1/admin/users/{MEMBER}/role", json={"tier": "platform_operator"},
                   headers={"X-Auth-User-Id": ADMIN})
    assert r.status_code == 403


def test_set_labels_admin_only(client, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "member")
    r = client.put("/v1/admin/role-labels", json={"manager": "Team Lead"},
                   headers={"X-Auth-User-Id": MEMBER})
    assert r.status_code == 403
