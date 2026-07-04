"""Phase 2a.2: gated per-agent dashboard MANAGEMENT proxy + status passthrough.

Mounts the manage router WITH app.state.config so admin_denied's tier check runs
(mirrors test_agent_admin_gate.py). All dashboard I/O is monkeypatched — these
are routing/gating tests, not integration tests.
"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.manage as manage
from src.api import authz
from src.api.manage import router as manage_router
from src.auth import require_bearer


@pytest.fixture
def app(tmp_path):
    application = FastAPI()
    application.state.config = SimpleNamespace(instance_id="sandbox", hermes_stack_dir=tmp_path)
    application.dependency_overrides[require_bearer] = lambda: None
    application.include_router(manage_router)
    return application


@pytest.fixture
def client(app, monkeypatch):
    monkeypatch.setattr(manage, "_dashboard_base", lambda agent: "http://127.0.0.1:9119")
    monkeypatch.setattr(manage, "_dashboard_headers", lambda: {"X-Hermes-Session-Token": "tok-abc"})
    return TestClient(app)


def _as(monkeypatch, tier):
    monkeypatch.setattr(authz.roles, "resolve_tier", lambda instance_id, user_id: tier)


class _Resp:
    def __init__(self, status=200, content=b"{}", content_type="application/json"):
        self.status_code = status
        self.content = content
        self.headers = {"content-type": content_type}


# --- allowlist unit -------------------------------------------------------

@pytest.mark.parametrize("sub,ok", [
    ("skills", True), ("skills/foo", True),
    ("cron/jobs", True), ("config/schema", True), ("env", True),
    ("env/API_KEY/reveal", True), ("model/options", True), ("profiles", True),
    ("logs", True), ("analytics/usage", True),
    ("dashboard/plugins", True), ("dashboard/plugins/x/enable", True),
    ("dashboard/plugin-providers", True), ("dashboard/plugin-providers/active", True),
    ("providers/oauth", True),
    ("sessions", False), ("sessions/s-1/messages", False), ("status", False),
    ("dashboard", False), ("providers", False), ("../etc/passwd", False),
    ("envy", False), ("", False),
    # Traversal via dot-segments: httpx normalizes "../" when building the
    # upstream URL, so a raw-string prefix match alone would let these reach
    # excluded endpoints (e.g. env/../../sessions -> /api/sessions). Must be
    # rejected before the prefix check.
    ("env/../../sessions", False), ("env/../status", False),
    ("skills/../../env", False), ("analytics/../sessions", False),
    ("env/.", False), ("config/../config", False),
])
def test_subpath_allowed(sub, ok):
    assert manage._subpath_allowed(sub) is ok


# --- gate ------------------------------------------------------------------

def test_member_denied_before_dashboard(client, monkeypatch):
    _as(monkeypatch, "member")
    called = []
    monkeypatch.setattr(manage.httpx, "request", lambda *a, **k: called.append(1) or _Resp())
    r = client.get("/v1/agents/real-estate/dashboard/env", headers={"X-Auth-User-Id": "u-1"})
    assert r.status_code == 403
    assert called == [], "dashboard must not be touched when the gate denies"


def test_admin_allowlisted_forwards_method_body_query_and_token(client, monkeypatch):
    _as(monkeypatch, "account_admin")
    seen = {}

    def fake_request(method, url, content=None, headers=None, timeout=None):
        seen.update(method=method, url=url, content=content, headers=headers)
        return _Resp(status=200, content=b'{"ok":true}')

    monkeypatch.setattr(manage.httpx, "request", fake_request)
    r = client.put(
        "/v1/agents/real-estate/dashboard/env?dry=1",
        content=b'{"key":"K","value":"V"}',
        headers={"X-Auth-User-Id": "admin-1", "content-type": "application/json"},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert seen["method"] == "PUT"
    assert seen["url"] == "http://127.0.0.1:9119/api/env?dry=1"
    assert seen["content"] == b'{"key":"K","value":"V"}'
    assert seen["headers"]["X-Hermes-Session-Token"] == "tok-abc"
    assert seen["headers"]["content-type"] == "application/json"


def test_admin_non_allowlisted_404_without_touching_dashboard(client, monkeypatch):
    _as(monkeypatch, "account_admin")
    called = []
    monkeypatch.setattr(manage.httpx, "request", lambda *a, **k: called.append(1) or _Resp())
    r = client.get("/v1/agents/real-estate/dashboard/sessions", headers={"X-Auth-User-Id": "admin-1"})
    assert r.status_code == 404
    assert called == []


def test_admin_dotdot_traversal_404_without_touching_dashboard(client, monkeypatch):
    # Regression for the allowlist-traversal bypass: "env/../../sessions" starts
    # with the allowed "env/" prefix under a naive raw-string check, but httpx
    # normalizes "../" when building the upstream URL, so it would actually
    # forward to /api/sessions (an endpoint the allowlist deliberately excludes).
    #
    # CONFIRMED (not hypothetical): Starlette's TestClient itself builds the
    # request through httpx.Request(...), which normalizes "../" segments in
    # the URL BEFORE the ASGI router ever sees the path. Sending this literal
    # URL through TestClient collapses it client-side to
    # "/v1/agents/real-estate/sessions" (the "dashboard/" segment and the
    # dot-segments are gone), which doesn't match this router's
    # "/v1/agents/{agent}/dashboard/{subpath:path}" route at all -> FastAPI's
    # own generic {"detail": "Not Found"} (capital F) fires, never reaching
    # `manage.py`'s allowlist 404 ({"detail": "Not found"}, lowercase f).
    # Verified directly: called upstream list stayed empty and the body was
    # the generic router 404, not our handler's.
    #
    # So this test cannot exercise the fixed `_subpath_allowed` logic through
    # TestClient at all -- it only proves the route is unreachable via a
    # dot-segment URL by construction of the HTTP client itself, which is a
    # useful but different safety property. The REAL regression lock for the
    # defect (raw subpath string containing dot-segments bypassing the
    # allowlist) is the unit-level test_subpath_allowed parametrization above
    # (e.g. "env/../../sessions" -> False), which calls `_subpath_allowed`
    # directly with the raw string and does not go through any URL/httpx
    # normalization.
    _as(monkeypatch, "account_admin")
    called = []
    monkeypatch.setattr(manage.httpx, "request", lambda *a, **k: called.append(1) or _Resp())
    r = client.get(
        "/v1/agents/real-estate/dashboard/env/../../sessions",
        headers={"X-Auth-User-Id": "admin-1"},
    )
    assert r.status_code == 404
    assert called == [], "dashboard must not be contacted for a dot-segment traversal subpath"


def test_identityless_internal_caller_allowed(client, monkeypatch):
    # No X-Auth-User-Id -> trusted internal -> passes the gate and forwards.
    monkeypatch.setattr(manage.httpx, "request", lambda *a, **k: _Resp(content=b"[]"))
    r = client.get("/v1/agents/real-estate/dashboard/skills")
    assert r.status_code == 200


# --- status passthrough (member-reachable) --------------------------------

def test_status_reachable_by_member(client, monkeypatch):
    _as(monkeypatch, "member")
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen.update(url=url, headers=headers)
        return _Resp(content=b'{"status":"running"}')

    monkeypatch.setattr(manage.httpx, "get", fake_get)
    r = client.get("/v1/agents/real-estate/status", headers={"X-Auth-User-Id": "u-1"})
    assert r.status_code == 200
    assert seen["url"] == "http://127.0.0.1:9119/api/status"
    assert seen["headers"]["X-Hermes-Session-Token"] == "tok-abc"


def test_dashboard_unconfigured_503(app, monkeypatch):
    monkeypatch.setattr(manage, "_dashboard_base", lambda agent: None)
    _as(monkeypatch, "account_admin")
    c = TestClient(app)
    r = c.get("/v1/agents/nope/dashboard/skills", headers={"X-Auth-User-Id": "admin-1"})
    assert r.status_code == 503
