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
    # Caller is account_admin; target (MEMBER) resolves strictly below caller,
    # and the assigned tier ("manager") is also strictly below caller -- allowed.
    monkeypatch.setattr(
        roles, "resolve_tier",
        lambda i, u: "member" if u == MEMBER else "account_admin",
    )
    writes, events = [], []
    monkeypatch.setattr(roles, "set_tier",
                        lambda inst, uid, tier, by: writes.append((inst, uid, tier, by)))
    monkeypatch.setattr(admin, "_emit_admin_event", lambda *a, **k: events.append(a))
    r = client.put(f"/v1/admin/users/{MEMBER}/role", json={"tier": "manager"},
                   headers={"X-Auth-User-Id": ADMIN})
    assert r.status_code == 200
    assert writes == [("sandbox", MEMBER, "manager", ADMIN)]
    assert len(events) == 1


def test_emit_admin_event_records_actual_caller_tier(client, monkeypatch):
    # The caller here is platform_operator (not the hardcoded "account_admin"
    # the audit used to write) -- the governance event must reflect that.
    monkeypatch.setattr(
        roles, "resolve_tier",
        lambda i, u: "member" if u == MEMBER else "platform_operator",
    )
    monkeypatch.setattr(roles, "set_tier", lambda inst, uid, tier, by: None)
    posted = {}

    class _Resp:
        def raise_for_status(self):
            pass

    def _fake_post(url, headers=None, json=None, timeout=None):
        posted["json"] = json
        return _Resp()

    monkeypatch.setenv("SUPABASE_URL", "http://sb")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "k")
    monkeypatch.setattr(admin.httpx, "post", _fake_post)
    r = client.put(f"/v1/admin/users/{MEMBER}/role", json={"tier": "manager"},
                   headers={"X-Auth-User-Id": ADMIN})
    assert r.status_code == 200
    assert posted["json"]["user_role"] == "platform_operator"


def test_account_admin_cannot_assign_platform_operator(client, monkeypatch):
    monkeypatch.setattr(
        roles, "resolve_tier",
        lambda i, u: "member" if u == MEMBER else "account_admin",
    )
    r = client.put(f"/v1/admin/users/{MEMBER}/role", json={"tier": "platform_operator"},
                   headers={"X-Auth-User-Id": ADMIN})
    assert r.status_code == 403


def test_account_admin_cannot_demote_platform_operator_target(client, monkeypatch):
    # Target already outranks the caller -- caller may not modify them at all,
    # regardless of the tier being assigned (lateral/lockout guard).
    monkeypatch.setattr(
        roles, "resolve_tier",
        lambda i, u: "platform_operator" if u == MEMBER else "account_admin",
    )
    r = client.put(f"/v1/admin/users/{MEMBER}/role", json={"tier": "member"},
                   headers={"X-Auth-User-Id": ADMIN})
    assert r.status_code == 403


def test_account_admin_cannot_assign_own_tier_to_peer(client, monkeypatch):
    # Target is below caller, but the assigned tier equals the caller's own tier --
    # still forbidden (may only assign strictly below own tier).
    monkeypatch.setattr(
        roles, "resolve_tier",
        lambda i, u: "member" if u == MEMBER else "account_admin",
    )
    r = client.put(f"/v1/admin/users/{MEMBER}/role", json={"tier": "account_admin"},
                   headers={"X-Auth-User-Id": ADMIN})
    assert r.status_code == 403


def test_account_admin_can_promote_member_to_manager(client, monkeypatch):
    # Both target's current tier and the assigned tier are strictly below caller.
    monkeypatch.setattr(
        roles, "resolve_tier",
        lambda i, u: "member" if u == MEMBER else "account_admin",
    )
    writes = []
    monkeypatch.setattr(roles, "set_tier",
                        lambda inst, uid, tier, by: writes.append((inst, uid, tier, by)))
    monkeypatch.setattr(admin, "_emit_admin_event", lambda *a, **k: None)
    r = client.put(f"/v1/admin/users/{MEMBER}/role", json={"tier": "manager"},
                   headers={"X-Auth-User-Id": ADMIN})
    assert r.status_code == 200
    assert writes == [("sandbox", MEMBER, "manager", ADMIN)]


def test_platform_operator_can_demote_another_platform_operator(client, monkeypatch):
    # Operator may do anything, including modifying/demoting another operator.
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "platform_operator")
    writes = []
    monkeypatch.setattr(roles, "set_tier",
                        lambda inst, uid, tier, by: writes.append((inst, uid, tier, by)))
    monkeypatch.setattr(admin, "_emit_admin_event", lambda *a, **k: None)
    r = client.put(f"/v1/admin/users/{MEMBER}/role", json={"tier": "member"},
                   headers={"X-Auth-User-Id": ADMIN})
    assert r.status_code == 200
    assert writes == [("sandbox", MEMBER, "member", ADMIN)]


def test_set_labels_admin_only(client, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "member")
    r = client.put("/v1/admin/role-labels", json={"manager": "Team Lead"},
                   headers={"X-Auth-User-Id": MEMBER})
    assert r.status_code == 403
