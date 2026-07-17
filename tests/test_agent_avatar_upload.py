import types
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.api.agents import router as agents_router
from src.api import agents as agents_mod
from src.api import roles
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
    monkeypatch.setattr(roles, "resolve_tier", lambda inst, uid: "account_admin")
    return TestClient(app), monkeypatch

def test_upload_posts_instance_scoped_path_and_returns_public_url(ctx):
    c, monkeypatch = ctx
    cap = {}
    def fake_post(url, content=None, headers=None, timeout=None):
        cap.update(url=url, content=content, headers=headers)
        return _Resp()
    monkeypatch.setattr(agents_mod.httpx, "post", fake_post)
    r = c.post("/v1/agents/ollie/avatar", content=b"\xff\xd8jpeg",
               headers={"X-Auth-User-Id": "u1", "Content-Type": "image/jpeg"})
    assert r.status_code == 200
    assert cap["url"].endswith("/storage/v1/object/agent-avatars/shared/sandbox/ollie.jpg")
    assert cap["headers"]["x-upsert"] == "true"
    assert cap["content"] == b"\xff\xd8jpeg"
    assert "/storage/v1/object/public/agent-avatars/shared/sandbox/ollie.jpg?t=" in r.json()["avatar_url"]

def test_upload_denied_for_member(ctx):
    c, monkeypatch = ctx
    monkeypatch.setattr(roles, "resolve_tier", lambda inst, uid: "member")
    monkeypatch.setattr(agents_mod.httpx, "post", lambda *a, **k: _Resp())
    r = c.post("/v1/agents/ollie/avatar", content=b"x",
               headers={"X-Auth-User-Id": "u1", "Content-Type": "image/jpeg"})
    assert r.status_code == 403

def test_upload_unknown_agent_404(ctx):
    c, monkeypatch = ctx
    monkeypatch.setattr(agents_mod.httpx, "post", lambda *a, **k: _Resp())
    r = c.post("/v1/agents/nope/avatar", content=b"x",
               headers={"X-Auth-User-Id": "u1", "Content-Type": "image/jpeg"})
    assert r.status_code == 404
