import types
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.api.agents import router as agents_router
from src.api import agents as agents_mod
from src.auth import require_bearer

_AGENTS = ('AGENTS_JSON=[{"id":"ollie","name":"Ollie",'
           '"gatewayUrl":"http://host.docker.internal:9100",'
           '"dashboardUrl":"http://host.docker.internal:9101","color":"#888","scope":"company"}]\n')


class _Resp:
    status_code = 200
    def raise_for_status(self): pass
    def json(self): return {}


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(_AGENTS)
    # Loopback Kong URL, as used server-side on the self-hosted boxes for the
    # orchestrator's own API/storage calls (SUPABASE_URL). The browser-facing
    # origin lives in SUPABASE_ISSUER and must be used for any URL handed back
    # to the browser (see test_post_avatar_mine_returns_issuer_derived_public_host).
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-key")
    monkeypatch.setenv("SUPABASE_ISSUER", "https://sb.example.co/auth/v1")
    app = FastAPI()
    app.state.config = types.SimpleNamespace(instance_id="sandbox", hermes_stack_dir=tmp_path)
    app.include_router(agents_router)
    app.dependency_overrides[require_bearer] = lambda: None
    return TestClient(app), monkeypatch


def test_post_avatar_mine_uploads_and_upserts_override(ctx):
    c, monkeypatch = ctx
    post_calls = []

    def fake_post(url, content=None, headers=None, json=None, timeout=None):
        post_calls.append(dict(url=url, content=content, headers=headers, json=json))
        return _Resp()

    monkeypatch.setattr(agents_mod.httpx, "post", fake_post)
    r = c.post("/v1/agents/ollie/avatar/mine", content=b"\xff\xd8jpeg",
               headers={"X-Auth-User-Id": "u1", "Content-Type": "image/jpeg"})
    assert r.status_code == 200
    assert len(post_calls) == 2

    storage_call = next(call for call in post_calls if call["url"].endswith("agent-avatars/u1/ollie.jpg"))
    assert storage_call["headers"]["x-upsert"] == "true"
    assert storage_call["content"] == b"\xff\xd8jpeg"

    upsert_call = next(call for call in post_calls if call["url"].endswith("/rest/v1/agent_avatar_overrides"))
    assert upsert_call["json"]["user_id"] == "u1"
    assert upsert_call["json"]["agent_id"] == "ollie"
    assert "avatar_url" in upsert_call["json"]
    assert upsert_call["headers"]["Prefer"] == "resolution=merge-duplicates,return=minimal"

    body = r.json()
    assert "/storage/v1/object/public/agent-avatars/u1/ollie.jpg?t=" in body["avatar_url"]


def test_post_avatar_mine_returns_issuer_derived_public_host(ctx):
    """The returned (and DB-persisted) avatar_url must use the browser-facing
    SUPABASE_ISSUER origin, not the loopback SUPABASE_URL used for the
    server-side upload/upsert calls — otherwise the browser can't load the
    image (sandbox 'olliesandbox', 2026-07-17)."""
    c, monkeypatch = ctx
    post_calls = []

    def fake_post(url, content=None, headers=None, json=None, timeout=None):
        post_calls.append(dict(url=url, json=json))
        return _Resp()

    monkeypatch.setattr(agents_mod.httpx, "post", fake_post)
    r = c.post("/v1/agents/ollie/avatar/mine", content=b"\xff\xd8jpeg",
               headers={"X-Auth-User-Id": "u1", "Content-Type": "image/jpeg"})
    assert r.status_code == 200

    storage_call = next(call for call in post_calls if call["url"].endswith("agent-avatars/u1/ollie.jpg"))
    # Upload POST still goes to the loopback Kong (SUPABASE_URL) — correct.
    assert storage_call["url"].startswith("http://127.0.0.1:8000/")

    upsert_call = next(call for call in post_calls if call["url"].endswith("/rest/v1/agent_avatar_overrides"))
    assert upsert_call["url"].startswith("http://127.0.0.1:8000/")
    # But the avatar_url handed back to the browser (and persisted for later
    # reads via GET /avatars/mine) must use the issuer-derived public host.
    assert upsert_call["json"]["avatar_url"].startswith(
        "https://sb.example.co/storage/v1/object/public/agent-avatars/u1/ollie.jpg?t="
    )

    body = r.json()
    assert body["avatar_url"].startswith(
        "https://sb.example.co/storage/v1/object/public/agent-avatars/u1/ollie.jpg?t="
    )


def test_post_avatar_mine_falls_back_to_supabase_url_when_no_issuer(ctx):
    """Non-split deployments have no SUPABASE_ISSUER set; the public URL should
    fall back to SUPABASE_URL's host rather than breaking."""
    c, monkeypatch = ctx
    monkeypatch.delenv("SUPABASE_ISSUER", raising=False)
    monkeypatch.setattr(agents_mod.httpx, "post", lambda *a, **k: _Resp())
    r = c.post("/v1/agents/ollie/avatar/mine", content=b"\xff\xd8jpeg",
               headers={"X-Auth-User-Id": "u1", "Content-Type": "image/jpeg"})
    assert r.status_code == 200
    body = r.json()
    assert body["avatar_url"].startswith(
        "http://127.0.0.1:8000/storage/v1/object/public/agent-avatars/u1/ollie.jpg?t="
    )


def test_post_avatar_mine_requires_identity(ctx):
    c, monkeypatch = ctx
    post_calls = []

    def fake_post(url, content=None, headers=None, json=None, timeout=None):
        post_calls.append(url)
        return _Resp()

    monkeypatch.setattr(agents_mod.httpx, "post", fake_post)
    r = c.post("/v1/agents/ollie/avatar/mine", content=b"\xff\xd8jpeg",
               headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 401
    assert post_calls == []


def test_post_avatar_mine_rejects_oversized_body(ctx):
    c, monkeypatch = ctx

    def fake_post(*a, **k):
        raise AssertionError("should not call supabase for oversized body")

    monkeypatch.setattr(agents_mod.httpx, "post", fake_post)
    oversized = b"x" * (5 * 1024 * 1024 + 1)
    r = c.post("/v1/agents/ollie/avatar/mine", content=oversized,
               headers={"X-Auth-User-Id": "u1", "Content-Type": "image/jpeg"})
    assert r.status_code == 413


def test_delete_avatar_mine_removes_override_row(ctx):
    c, monkeypatch = ctx
    delete_calls = []

    def fake_delete(url, params=None, headers=None, timeout=None):
        delete_calls.append(dict(url=url, params=params, headers=headers))
        return _Resp()

    monkeypatch.setattr(agents_mod.httpx, "delete", fake_delete)
    r = c.delete("/v1/agents/ollie/avatar/mine", headers={"X-Auth-User-Id": "u1"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    row_call = next(call for call in delete_calls if "/rest/v1/agent_avatar_overrides" in call["url"])
    assert row_call["params"]["user_id"] == "eq.u1"
    assert row_call["params"]["agent_id"] == "eq.ollie"


def test_delete_avatar_mine_rejects_malformed_agent_id(ctx):
    c, monkeypatch = ctx
    delete_calls = []

    def fake_delete(url, params=None, headers=None, timeout=None):
        delete_calls.append(dict(url=url, params=params, headers=headers))
        return _Resp()

    monkeypatch.setattr(agents_mod.httpx, "delete", fake_delete)
    # "AB" fails _NAME_RE (uppercase, too short) and never reaches Supabase.
    r = c.delete("/v1/agents/AB/avatar/mine", headers={"X-Auth-User-Id": "u1"})
    assert r.status_code == 404
    assert delete_calls == []


def test_get_avatars_mine_returns_override_map(ctx):
    c, monkeypatch = ctx

    def fake_get(url, params=None, headers=None, timeout=None):
        assert "/rest/v1/agent_avatar_overrides" in url
        assert params["user_id"] == "eq.u1"

        class _R:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return [{"agent_id": "ollie", "avatar_url": "https://x/u1/ollie.jpg"}]
        return _R()

    monkeypatch.setattr(agents_mod.httpx, "get", fake_get)
    r = c.get("/v1/agents/avatars/mine", headers={"X-Auth-User-Id": "u1"})
    assert r.status_code == 200
    assert r.json() == {"overrides": {"ollie": "https://x/u1/ollie.jpg"}}


def test_get_avatars_mine_no_identity_returns_empty(ctx):
    c, monkeypatch = ctx

    def fake_get(url, headers=None, timeout=None):
        raise AssertionError("should not call supabase when no identity")

    monkeypatch.setattr(agents_mod.httpx, "get", fake_get)
    r = c.get("/v1/agents/avatars/mine")
    assert r.status_code == 200
    assert r.json() == {"overrides": {}}


def test_get_avatars_mine_502_on_upstream_error_hides_internal_url(ctx):
    c, monkeypatch = ctx

    def fake_get(url, params=None, headers=None, timeout=None):
        request = httpx.Request("GET", url)
        response = httpx.Response(503, request=request)
        raise httpx.HTTPStatusError("server error", request=request, response=response)

    monkeypatch.setattr(agents_mod.httpx, "get", fake_get)
    r = c.get("/v1/agents/avatars/mine", headers={"X-Auth-User-Id": "u1"})
    assert r.status_code == 502
    assert "127.0.0.1" not in r.text
    assert "8000" not in r.text
    assert r.json() == {"detail": "upstream database error"}
