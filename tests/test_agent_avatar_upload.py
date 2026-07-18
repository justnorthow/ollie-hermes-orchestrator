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
    # Loopback Kong URL, as used server-side on the self-hosted boxes for the
    # orchestrator's own API/storage calls (SUPABASE_URL). The browser-facing
    # origin lives in SUPABASE_ISSUER and must be used for any URL handed back
    # to the browser (see test_upload_returns_issuer_derived_public_host below).
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-key")
    monkeypatch.setenv("SUPABASE_ISSUER", "https://sb.example.co/auth/v1")
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

def test_upload_returns_issuer_derived_public_host(ctx):
    """The returned avatar_url must use the browser-facing SUPABASE_ISSUER
    origin, not the loopback SUPABASE_URL used for the server-side upload —
    otherwise the browser can't load the image (sandbox 'olliesandbox', 2026-07-17)."""
    c, monkeypatch = ctx
    cap = {}
    def fake_post(url, content=None, headers=None, timeout=None):
        cap.update(url=url)
        return _Resp()
    monkeypatch.setattr(agents_mod.httpx, "post", fake_post)
    r = c.post("/v1/agents/ollie/avatar", content=b"\xff\xd8jpeg",
               headers={"X-Auth-User-Id": "u1", "Content-Type": "image/jpeg"})
    assert r.status_code == 200
    # Upload POST still goes to the loopback Kong (SUPABASE_URL) — correct, it's
    # a server-side call.
    assert cap["url"].startswith("http://127.0.0.1:8000/")
    # But the URL handed back to the browser must use the issuer-derived
    # public host.
    avatar_url = r.json()["avatar_url"]
    assert avatar_url.startswith(
        "https://sb.example.co/storage/v1/object/public/agent-avatars/shared/sandbox/ollie.jpg?t="
    )

def test_upload_falls_back_to_supabase_url_when_no_issuer(ctx):
    """Non-split deployments have no SUPABASE_ISSUER set; the public URL should
    fall back to SUPABASE_URL's host rather than breaking."""
    c, monkeypatch = ctx
    monkeypatch.delenv("SUPABASE_ISSUER", raising=False)
    monkeypatch.setattr(agents_mod.httpx, "post", lambda *a, **k: _Resp())
    r = c.post("/v1/agents/ollie/avatar", content=b"\xff\xd8jpeg",
               headers={"X-Auth-User-Id": "u1", "Content-Type": "image/jpeg"})
    assert r.status_code == 200
    avatar_url = r.json()["avatar_url"]
    assert avatar_url.startswith(
        "http://127.0.0.1:8000/storage/v1/object/public/agent-avatars/shared/sandbox/ollie.jpg?t="
    )

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
