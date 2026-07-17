import types
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
    monkeypatch.setenv("SUPABASE_URL", "https://sb.example.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-key")
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
