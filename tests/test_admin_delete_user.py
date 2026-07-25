"""DELETE /v1/admin/users/{user_id} -- full user removal (Task A1, delete-user feature).

Supabase HTTP is mocked by monkeypatching admin.httpx.delete directly (this repo's
existing admin-test harness pattern -- see tests/test_admin_api.py), not respx.
roles.py write/read functions are monkeypatched individually per test.
"""
import types
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import src.api.admin as admin
import src.api.roles as roles
from src.api.admin import router as admin_router
from src.auth import require_bearer

ADMIN = "aaaaaaaa-0000-0000-0000-00000000000a"
MEMBER = "mmmmmmmm-0000-0000-0000-00000000000m"
OTHER_ADMIN = "bbbbbbbb-0000-0000-0000-00000000000b"


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.state.config = types.SimpleNamespace(instance_id="sandbox", hermes_stack_dir=None)
    app.include_router(admin_router)
    app.dependency_overrides[require_bearer] = lambda: None
    monkeypatch.setenv("SUPABASE_URL", "https://sb.example")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    return TestClient(app)


def test_delete_user_happy_path(client, monkeypatch):
    # caller is platform_operator; target is a member; another admin exists
    monkeypatch.setattr(roles, "resolve_tier",
                        lambda inst, uid: "platform_operator" if uid == ADMIN else "member")
    monkeypatch.setattr(roles, "list_roles",
                        lambda inst: {ADMIN: "platform_operator", MEMBER: "member"})
    seen = {}
    monkeypatch.setattr(roles, "delete_user_rows",
                        lambda inst, uid: seen.update(rows=(inst, uid)))
    monkeypatch.setattr(admin, "_delete_auth_user",
                        lambda uid: seen.update(auth=uid) or 200)
    r = client.delete(f"/v1/admin/users/{MEMBER}", headers={"X-Auth-User-Id": ADMIN})
    assert r.status_code == 200
    assert r.json() == {"userId": MEMBER, "deleted": True}
    assert seen["rows"] == ("sandbox", MEMBER)
    assert seen["auth"] == MEMBER  # auth delete attempted after the row delete


def test_delete_user_self_forbidden(client, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda inst, uid: "account_admin")
    r = client.delete(f"/v1/admin/users/{ADMIN}", headers={"X-Auth-User-Id": ADMIN})
    assert r.status_code == 403


def test_delete_user_tier_ceiling_forbidden(client, monkeypatch):
    # caller is account_admin; target is also account_admin (>= caller) -> forbidden
    monkeypatch.setattr(roles, "resolve_tier",
                        lambda inst, uid: "account_admin")
    monkeypatch.setattr(roles, "list_roles",
                        lambda inst: {ADMIN: "account_admin", OTHER_ADMIN: "account_admin"})
    r = client.delete(f"/v1/admin/users/{OTHER_ADMIN}", headers={"X-Auth-User-Id": ADMIN})
    assert r.status_code == 403


def test_delete_user_last_admin_conflict(client, monkeypatch):
    # caller is platform_operator; target is the ONLY account_admin+ -> 409
    monkeypatch.setattr(roles, "resolve_tier",
                        lambda inst, uid: "platform_operator" if uid == ADMIN else "account_admin")
    monkeypatch.setattr(roles, "list_roles",
                        lambda inst: {OTHER_ADMIN: "account_admin"})  # the only admin, and it's the target
    r = client.delete(f"/v1/admin/users/{OTHER_ADMIN}", headers={"X-Auth-User-Id": ADMIN})
    assert r.status_code == 409


def test_delete_user_not_found(client, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier",
                        lambda inst, uid: "platform_operator" if uid == ADMIN else "member")
    monkeypatch.setattr(roles, "list_roles", lambda inst: {ADMIN: "platform_operator"})
    monkeypatch.setattr(roles, "delete_user_rows", lambda inst, uid: None)
    monkeypatch.setattr(admin, "_delete_auth_user", lambda uid: 404)
    r = client.delete(f"/v1/admin/users/{MEMBER}", headers={"X-Auth-User-Id": ADMIN})
    assert r.status_code == 404


def test_delete_user_requires_admin(client, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda inst, uid: "member")
    r = client.delete(f"/v1/admin/users/{OTHER_ADMIN}", headers={"X-Auth-User-Id": MEMBER})
    assert r.status_code == 403


def test_delete_user_no_supabase(client, monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setattr(roles, "resolve_tier",
                        lambda inst, uid: "platform_operator" if uid == ADMIN else "member")
    r = client.delete(f"/v1/admin/users/{MEMBER}", headers={"X-Auth-User-Id": ADMIN})
    assert r.status_code == 400
