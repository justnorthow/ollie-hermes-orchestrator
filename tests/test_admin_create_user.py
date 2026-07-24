"""POST /v1/admin/users -- create+invite+provision a Supabase auth user (Task A1).

Supabase HTTP is mocked by monkeypatching admin.httpx.get/post directly (this repo's
existing admin-test harness pattern -- see tests/test_admin_api.py's _fake_post), not
respx. roles.py write/read functions are monkeypatched individually per test.
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
MGR = "111111mm-0000-0000-0000-00000000000m"


@pytest.fixture
def client():
    app = FastAPI()
    app.state.config = types.SimpleNamespace(instance_id="sandbox", hermes_stack_dir=None)
    app.include_router(admin_router)
    app.dependency_overrides[require_bearer] = lambda: None
    return TestClient(app)


def admin_headers(uid: str) -> dict:
    return {"X-Auth-User-Id": uid}


class _Resp:
    """Fake httpx.Response: only .json() and .raise_for_status() are exercised."""

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fake_get(payload):
    def _get(url, params=None, headers=None, timeout=None):
        return _Resp(payload)
    return _get


def _fake_post(calls, link_payload=None):
    """Routes admin.httpx.post calls: a generate_link URL returns link_payload;
    anything else (e.g. the governance_events call from _emit_admin_event)
    returns an empty, non-raising response."""
    def _post(url, headers=None, json=None, timeout=None):
        calls.append((url, json))
        if "generate_link" in url:
            return _Resp(link_payload)
        return _Resp({})
    return _post


def test_create_user_new_invites_and_provisions(client, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://sb.example")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    # caller is account_admin
    monkeypatch.setattr(roles, "resolve_tier",
                        lambda inst, uid: "account_admin" if uid == ADMIN else "member")
    # no existing auth user with this email
    monkeypatch.setattr(admin.httpx, "get", _fake_get({"users": []}))
    post_calls = []
    monkeypatch.setattr(admin.httpx, "post", _fake_post(
        post_calls,
        link_payload={"user": {"id": "u-new"}, "action_link": "https://site/invite#t=abc"},
    ))
    seen = {}
    monkeypatch.setattr(roles, "set_tier",
                        lambda inst, uid, tier, by: seen.update(tier=(uid, tier)))
    monkeypatch.setattr(roles, "set_user_tags",
                        lambda uid, tags: seen.update(tags=(uid, tags)))
    monkeypatch.setattr(roles, "set_governance_view",
                        lambda inst, uid, en: seen.update(gov=(uid, en)))

    r = client.post("/v1/admin/users", headers=admin_headers(ADMIN),
                    json={"email": "new@brk.com", "tier": "manager",
                          "tags": ["compliance"], "governanceView": True})

    assert r.status_code == 200
    assert r.json() == {"userId": "u-new", "email": "new@brk.com",
                        "inviteLink": "https://site/invite#t=abc"}
    assert seen["tier"] == ("u-new", "manager")
    assert seen["tags"] == ("u-new", ["compliance"])
    assert seen["gov"] == ("u-new", True)
    # the new-user branch must request an "invite" link, not "magiclink"
    link_calls = [j for u, j in post_calls if "generate_link" in u]
    assert len(link_calls) == 1
    assert link_calls[0]["type"] == "invite"
    assert link_calls[0]["email"] == "new@brk.com"


def test_create_user_already_configured_conflicts(client, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://sb.example")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    monkeypatch.setattr(roles, "resolve_tier", lambda inst, uid: "account_admin")
    monkeypatch.setattr(admin.httpx, "get", _fake_get(
        {"users": [{"id": "u-exist", "email": "dup@brk.com"}]}))
    # already has a user_roles row on this instance
    monkeypatch.setattr(roles, "list_roles", lambda inst: {"u-exist": "member"})
    post_calls = []
    monkeypatch.setattr(admin.httpx, "post", _fake_post(post_calls))

    r = client.post("/v1/admin/users", headers=admin_headers(ADMIN),
                    json={"email": "dup@brk.com", "tier": "manager",
                          "tags": [], "governanceView": False})

    assert r.status_code == 409
    # must not have generated a link or provisioned anything for a conflict
    assert not [j for u, j in post_calls if "generate_link" in u]


def test_create_user_exists_but_unconfigured_reprovisions(client, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://sb.example")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    monkeypatch.setattr(roles, "resolve_tier", lambda inst, uid: "account_admin")
    monkeypatch.setattr(admin.httpx, "get", _fake_get(
        {"users": [{"id": "u-exist", "email": "half@brk.com"}]}))
    monkeypatch.setattr(roles, "list_roles", lambda inst: {})  # not configured here
    post_calls = []
    monkeypatch.setattr(admin.httpx, "post", _fake_post(
        post_calls,
        link_payload={"user": {"id": "u-exist"}, "action_link": "https://site/invite#t=zzz"},
    ))
    seen = {}
    monkeypatch.setattr(roles, "set_tier",
                        lambda inst, uid, tier, by: seen.update(tier=(uid, tier)))
    monkeypatch.setattr(roles, "set_user_tags",
                        lambda uid, tags: seen.update(tags=(uid, tags)))
    monkeypatch.setattr(roles, "set_governance_view",
                        lambda inst, uid, en: seen.update(gov=(uid, en)))

    r = client.post("/v1/admin/users", headers=admin_headers(ADMIN),
                    json={"email": "half@brk.com", "tier": "member",
                          "tags": [], "governanceView": False})

    assert r.status_code == 200
    assert r.json()["userId"] == "u-exist"
    assert seen["tier"] == ("u-exist", "member")
    # exists-but-unconfigured branch must request "magiclink", not "invite"
    link_calls = [j for u, j in post_calls if "generate_link" in u]
    assert len(link_calls) == 1
    assert link_calls[0]["type"] == "magiclink"


def test_create_user_bad_tier_422(client, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda inst, uid: "account_admin")

    r = client.post("/v1/admin/users", headers=admin_headers(ADMIN),
                    json={"email": "x@brk.com", "tier": "wizard",
                          "tags": [], "governanceView": False})

    assert r.status_code == 422


def test_create_user_tier_guard_forbids_minting_at_or_above_self(client, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://sb.example")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    monkeypatch.setattr(roles, "resolve_tier", lambda inst, uid: "manager")  # caller is manager

    r = client.post("/v1/admin/users", headers=admin_headers(MGR),
                    json={"email": "x@brk.com", "tier": "account_admin",
                          "tags": [], "governanceView": False})

    assert r.status_code == 403


def test_create_user_no_supabase_400(client, monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.setattr(roles, "resolve_tier", lambda inst, uid: "account_admin")

    r = client.post("/v1/admin/users", headers=admin_headers(ADMIN),
                    json={"email": "x@brk.com", "tier": "member",
                          "tags": [], "governanceView": False})

    assert r.status_code == 400
