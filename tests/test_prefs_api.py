"""Member-scoped prefs endpoints (service role, self-hosted ES256 fix).

Mirrors tests/test_profile_api.py: PostgREST rejects the browser's ES256
user token on the self-hosted boxes, so the frontend prefs store's
load/save calls go through the orchestrator's service role instead, scoped
by the trusted X-Auth-User-Id header.
"""
import types

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import prefs as prefs_mod
from src.api.prefs import router as prefs_router
from src.auth import require_bearer


class _Resp:
    status_code = 200
    def raise_for_status(self): pass
    def json(self): return []


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    app = FastAPI()
    app.state.config = types.SimpleNamespace(instance_id="sandbox")
    app.include_router(prefs_router)
    app.dependency_overrides[require_bearer] = lambda: None
    return TestClient(app), monkeypatch


# ---------------------------------------------------------------------------
# GET /v1/prefs/mine
# ---------------------------------------------------------------------------

def test_get_mine_scoped_select_returns_prefs(ctx):
    c, monkeypatch = ctx
    get_calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        get_calls.append(dict(url=url, params=params, headers=headers))
        class _R:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return [{"user_id": "u1", "prefs": {"theme": "dark"}}]
        return _R()

    monkeypatch.setattr(prefs_mod.httpx, "get", fake_get)
    r = c.get("/v1/prefs/mine", headers={"X-Auth-User-Id": "u1"})
    assert r.status_code == 200
    assert r.json() == {"prefs": {"theme": "dark"}}

    assert len(get_calls) == 1
    call = get_calls[0]
    assert call["url"].endswith("/rest/v1/user_prefs")
    assert call["params"]["user_id"] == "eq.u1"
    assert call["params"]["select"] == "prefs"
    assert call["headers"]["apikey"] == "svc"
    assert call["headers"]["Authorization"] == "Bearer svc"


def test_get_mine_no_row_returns_null_prefs(ctx):
    c, monkeypatch = ctx

    def fake_get(url, params=None, headers=None, timeout=None):
        return _Resp()

    monkeypatch.setattr(prefs_mod.httpx, "get", fake_get)
    r = c.get("/v1/prefs/mine", headers={"X-Auth-User-Id": "u1"})
    assert r.status_code == 200
    assert r.json() == {"prefs": None}


def test_get_mine_no_identity_lenient_null_prefs(ctx):
    c, monkeypatch = ctx

    def fake_get(*a, **k):
        raise AssertionError("should not call supabase when no identity")

    monkeypatch.setattr(prefs_mod.httpx, "get", fake_get)
    r = c.get("/v1/prefs/mine")
    assert r.status_code == 200
    assert r.json() == {"prefs": None}


def test_get_mine_502_on_upstream_error_hides_internal_url(ctx):
    c, monkeypatch = ctx

    def fake_get(url, params=None, headers=None, timeout=None):
        request = httpx.Request("GET", url)
        response = httpx.Response(503, request=request)
        raise httpx.HTTPStatusError("server error", request=request, response=response)

    monkeypatch.setattr(prefs_mod.httpx, "get", fake_get)
    r = c.get("/v1/prefs/mine", headers={"X-Auth-User-Id": "u1"})
    assert r.status_code == 502
    assert "127.0.0.1" not in r.text
    assert "8000" not in r.text
    assert r.json() == {"detail": "upstream database error"}


# ---------------------------------------------------------------------------
# PUT /v1/prefs/mine
# ---------------------------------------------------------------------------

def test_put_mine_upserts_with_trusted_user_id_ignoring_body(ctx):
    c, monkeypatch = ctx
    post_calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        post_calls.append(dict(url=url, headers=headers, json=json))
        return _Resp()

    monkeypatch.setattr(prefs_mod.httpx, "post", fake_post)
    body = {"user_id": "evil", "prefs": {"theme": "dark", "sidebar": "collapsed"}}
    r = c.put("/v1/prefs/mine", json=body, headers={"X-Auth-User-Id": "u1"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    assert len(post_calls) == 1
    call = post_calls[0]
    assert call["url"].endswith("/rest/v1/user_prefs")
    payload = call["json"]
    assert payload["user_id"] == "u1"
    assert payload["prefs"] == {"theme": "dark", "sidebar": "collapsed"}
    assert "updated_at" in payload
    assert call["headers"]["Prefer"] == "resolution=merge-duplicates,return=minimal"
    assert call["headers"]["Content-Type"] == "application/json"


def test_put_mine_requires_identity(ctx):
    c, monkeypatch = ctx

    def fake_post(*a, **k):
        raise AssertionError("should not call supabase when no identity")

    monkeypatch.setattr(prefs_mod.httpx, "post", fake_post)
    r = c.put("/v1/prefs/mine", json={"prefs": {}})
    assert r.status_code == 401


def test_put_mine_503_when_supabase_not_configured(ctx):
    c, monkeypatch = ctx
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    r = c.put("/v1/prefs/mine", json={"prefs": {}}, headers={"X-Auth-User-Id": "u1"})
    assert r.status_code == 503


def test_put_mine_502_on_upstream_error_hides_internal_url(ctx):
    c, monkeypatch = ctx

    def fake_post(url, headers=None, json=None, timeout=None):
        request = httpx.Request("POST", url)
        response = httpx.Response(503, request=request)
        raise httpx.HTTPStatusError("server error", request=request, response=response)

    monkeypatch.setattr(prefs_mod.httpx, "post", fake_post)
    r = c.put("/v1/prefs/mine", json={"prefs": {}}, headers={"X-Auth-User-Id": "u1"})
    assert r.status_code == 502
    assert "127.0.0.1" not in r.text
    assert "8000" not in r.text
    assert r.json() == {"detail": "upstream database error"}
