"""Member-scoped profile endpoints (service role, self-hosted ES256 fix).

On the self-hosted boxes, PostgREST/storage-api reject the browser's ES256
user token, so Profile.tsx's load/save/upload calls must go through the
orchestrator's service role instead, scoped by the trusted X-Auth-User-Id
header (set by nginx's cryptographic auth_request; unforgeable by the
browser). Mirrors the pattern in tests/test_agent_avatar_mine.py.
"""
import types

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import profile as profile_mod
from src.api.profile import router as profile_router
from src.auth import require_bearer


class _Resp:
    status_code = 200
    def raise_for_status(self): pass
    def json(self): return []


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    monkeypatch.setenv("SUPABASE_ISSUER", "https://sb.example.co/auth/v1")
    app = FastAPI()
    app.state.config = types.SimpleNamespace(instance_id="sandbox", hermes_stack_dir=tmp_path)
    app.include_router(profile_router)
    app.dependency_overrides[require_bearer] = lambda: None
    return TestClient(app), monkeypatch


# ---------------------------------------------------------------------------
# GET /v1/profile/mine
# ---------------------------------------------------------------------------

def test_get_mine_scoped_select_returns_row_and_email(ctx):
    c, monkeypatch = ctx
    get_calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        get_calls.append(dict(url=url, params=params, headers=headers))
        class _R:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return [{"user_id": "u1", "title": "REALTOR"}]
        return _R()

    monkeypatch.setattr(profile_mod.httpx, "get", fake_get)
    r = c.get("/v1/profile/mine", headers={"X-Auth-User-Id": "u1", "X-Auth-Email": "a@b.co"})
    assert r.status_code == 200
    body = r.json()
    assert body == {"profile": {"user_id": "u1", "title": "REALTOR"}, "email": "a@b.co"}

    assert len(get_calls) == 1
    call = get_calls[0]
    assert call["url"].endswith("/rest/v1/profiles")
    assert call["params"]["user_id"] == "eq.u1"
    assert call["headers"]["apikey"] == "svc"
    assert call["headers"]["Authorization"] == "Bearer svc"


def test_get_mine_no_row_returns_null_profile(ctx):
    c, monkeypatch = ctx

    def fake_get(url, params=None, headers=None, timeout=None):
        return _Resp()

    monkeypatch.setattr(profile_mod.httpx, "get", fake_get)
    r = c.get("/v1/profile/mine", headers={"X-Auth-User-Id": "u1", "X-Auth-Email": "a@b.co"})
    assert r.status_code == 200
    assert r.json() == {"profile": None, "email": "a@b.co"}


def test_get_mine_requires_identity(ctx):
    c, monkeypatch = ctx

    def fake_get(*a, **k):
        raise AssertionError("should not call supabase when no identity")

    monkeypatch.setattr(profile_mod.httpx, "get", fake_get)
    r = c.get("/v1/profile/mine")
    assert r.status_code == 401


def test_get_mine_502_on_upstream_error_hides_internal_url(ctx):
    c, monkeypatch = ctx

    def fake_get(url, params=None, headers=None, timeout=None):
        request = httpx.Request("GET", url)
        response = httpx.Response(503, request=request)
        raise httpx.HTTPStatusError("server error", request=request, response=response)

    monkeypatch.setattr(profile_mod.httpx, "get", fake_get)
    r = c.get("/v1/profile/mine", headers={"X-Auth-User-Id": "u1", "X-Auth-Email": "a@b.co"})
    assert r.status_code == 502
    assert "127.0.0.1" not in r.text
    assert "8000" not in r.text
    assert r.json() == {"detail": "upstream database error"}


# ---------------------------------------------------------------------------
# PUT /v1/profile/mine
# ---------------------------------------------------------------------------

def test_put_mine_whitelists_columns_and_forces_trusted_user_id(ctx):
    c, monkeypatch = ctx
    post_calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        post_calls.append(dict(url=url, headers=headers, json=json))
        return _Resp()

    monkeypatch.setattr(profile_mod.httpx, "post", fake_post)
    body = {
        "user_id": "evil",
        "role": "broker",
        "title": "REALTOR",
        "phone": "555-1234",
        "market_area": "Williamson",
    }
    r = c.put("/v1/profile/mine", json=body, headers={"X-Auth-User-Id": "u1"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    assert len(post_calls) == 1
    call = post_calls[0]
    assert call["url"].endswith("/rest/v1/profiles")
    payload = call["json"]
    assert payload["user_id"] == "u1"
    assert "role" not in payload
    assert payload["title"] == "REALTOR"
    assert payload["phone"] == "555-1234"
    assert payload["market_area"] == "Williamson"
    assert "updated_at" in payload
    assert call["headers"]["Prefer"] == "resolution=merge-duplicates,return=minimal"
    assert call["headers"]["Content-Type"] == "application/json"


def test_put_mine_requires_identity(ctx):
    c, monkeypatch = ctx

    def fake_post(*a, **k):
        raise AssertionError("should not call supabase when no identity")

    monkeypatch.setattr(profile_mod.httpx, "post", fake_post)
    r = c.put("/v1/profile/mine", json={"title": "x"})
    assert r.status_code == 401


def test_put_mine_503_when_supabase_not_configured(ctx, monkeypatch_env=None):
    c, monkeypatch = ctx
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    r = c.put("/v1/profile/mine", json={"title": "x"}, headers={"X-Auth-User-Id": "u1"})
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# POST /v1/profile/image/{kind}
# ---------------------------------------------------------------------------

def test_post_image_headshot_uploads_and_returns_issuer_url(ctx):
    c, monkeypatch = ctx
    post_calls = []

    def fake_post(url, content=None, headers=None, timeout=None):
        post_calls.append(dict(url=url, content=content, headers=headers))
        return _Resp()

    monkeypatch.setattr(profile_mod.httpx, "post", fake_post)
    r = c.post("/v1/profile/image/headshot", content=b"\xff\xd8jpeg",
                headers={"X-Auth-User-Id": "u1", "Content-Type": "image/jpeg"})
    assert r.status_code == 200

    assert len(post_calls) == 1
    call = post_calls[0]
    assert call["url"].endswith("/storage/v1/object/profile-images/u1/headshot.jpg")
    assert call["headers"]["x-upsert"] == "true"
    assert call["content"] == b"\xff\xd8jpeg"

    body = r.json()
    assert body["url"].startswith(
        "https://sb.example.co/storage/v1/object/public/profile-images/u1/headshot.jpg?t="
    )


def test_post_image_logo_uses_logo_path(ctx):
    c, monkeypatch = ctx
    post_calls = []

    def fake_post(url, content=None, headers=None, timeout=None):
        post_calls.append(url)
        return _Resp()

    monkeypatch.setattr(profile_mod.httpx, "post", fake_post)
    r = c.post("/v1/profile/image/logo", content=b"\xff\xd8jpeg",
                headers={"X-Auth-User-Id": "u1"})
    assert r.status_code == 200
    assert post_calls[0].endswith("/storage/v1/object/profile-images/u1/logo.jpg")


def test_post_image_unknown_kind_404(ctx):
    c, monkeypatch = ctx

    def fake_post(*a, **k):
        raise AssertionError("should not call supabase for unknown kind")

    monkeypatch.setattr(profile_mod.httpx, "post", fake_post)
    r = c.post("/v1/profile/image/bogus", content=b"\xff\xd8jpeg",
                headers={"X-Auth-User-Id": "u1"})
    assert r.status_code == 404


def test_post_image_requires_identity(ctx):
    c, monkeypatch = ctx

    def fake_post(*a, **k):
        raise AssertionError("should not call supabase when no identity")

    monkeypatch.setattr(profile_mod.httpx, "post", fake_post)
    r = c.post("/v1/profile/image/headshot", content=b"\xff\xd8jpeg")
    assert r.status_code == 401


def test_post_image_empty_body_400(ctx):
    c, monkeypatch = ctx

    def fake_post(*a, **k):
        raise AssertionError("should not call supabase for empty body")

    monkeypatch.setattr(profile_mod.httpx, "post", fake_post)
    r = c.post("/v1/profile/image/headshot", content=b"",
                headers={"X-Auth-User-Id": "u1"})
    assert r.status_code == 400
