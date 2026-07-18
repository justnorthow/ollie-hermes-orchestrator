"""Owner-filtered session endpoints (agent instantiation Phase 1).

All Supabase + dashboard I/O is monkeypatched; these are routing/enforcement tests.
"""
import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.sessions as sessions
from src.api.sessions import router as sessions_router
from src.auth import require_bearer

USER_A = "aaaaaaaa-0000-0000-0000-000000000001"
USER_B = "bbbbbbbb-0000-0000-0000-000000000002"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_URLS", json.dumps({"real-estate": "http://127.0.0.1:9119"}))
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-key")
    app = FastAPI()
    app.include_router(sessions_router)
    app.dependency_overrides[require_bearer] = lambda: None
    return TestClient(app)


def test_list_requires_identity(client):
    r = client.get("/v1/sessions/real-estate")
    assert r.status_code == 403


def test_dashboard_get_sends_session_token_when_set(monkeypatch):
    """The native Hermes dashboard 401s without X-Hermes-Session-Token even on
    loopback; _dashboard_get must forward it from HERMES_DASHBOARD_TOKEN."""
    monkeypatch.setenv("HERMES_DASHBOARD_URLS", json.dumps({"real-estate": "http://127.0.0.1:9119"}))
    monkeypatch.setenv("HERMES_DASHBOARD_TOKEN", "tok-abc")
    captured = {}

    class _Resp:
        status_code = 200
        content = b"[]"

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return _Resp()

    monkeypatch.setattr(sessions.httpx, "get", fake_get)
    status, _ = sessions._dashboard_get("real-estate", "/api/sessions/s-1/messages")
    assert status == 200
    assert captured["headers"]["X-Hermes-Session-Token"] == "tok-abc"


def test_dashboard_delete_sends_session_token_when_set(monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_URLS", json.dumps({"real-estate": "http://127.0.0.1:9119"}))
    monkeypatch.setenv("HERMES_DASHBOARD_TOKEN", "tok-xyz")
    captured = {}

    class _Resp:
        status_code = 200
        content = b"{}"

    def fake_delete(url, headers=None, timeout=None):
        captured["headers"] = headers
        return _Resp()

    monkeypatch.setattr(sessions.httpx, "delete", fake_delete)
    sessions._dashboard_delete("real-estate", "/api/sessions/s-1")
    assert captured["headers"]["X-Hermes-Session-Token"] == "tok-xyz"


def test_dashboard_headers_empty_when_token_unset(monkeypatch):
    monkeypatch.delenv("HERMES_DASHBOARD_TOKEN", raising=False)
    assert sessions._dashboard_headers() == {}


def test_dashboard_get_scopes_request_to_agent_profile(monkeypatch):
    """Hermes 0.18.2 unified the dashboards: `hermes -p <profile> dashboard`
    re-execs as the DEFAULT profile's dashboard, which serves the default
    profile's session DB unless ?profile=<name> is passed. Found 2026-07-18:
    every pam session read 404'd after the 0.18.2 update because the proxy
    hit the default profile's DB. Older per-profile dashboards ignore the
    extra param, so sending it unconditionally is safe."""
    monkeypatch.setenv("HERMES_DASHBOARD_URLS", json.dumps({"pam": "http://127.0.0.1:9122"}))
    captured = {}

    class _Resp:
        status_code = 200
        content = b"[]"

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        return _Resp()

    monkeypatch.setattr(sessions.httpx, "get", fake_get)
    sessions._dashboard_get("pam", "/api/sessions/s-1/messages")
    assert captured["url"] == "http://127.0.0.1:9122/api/sessions/s-1/messages?profile=pam"


def test_dashboard_delete_scopes_request_to_agent_profile(monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_URLS", json.dumps({"pam": "http://127.0.0.1:9122"}))
    captured = {}

    class _Resp:
        status_code = 200
        content = b"{}"

    def fake_delete(url, headers=None, timeout=None):
        captured["url"] = url
        return _Resp()

    monkeypatch.setattr(sessions.httpx, "delete", fake_delete)
    sessions._dashboard_delete("pam", "/api/sessions/s-1")
    assert captured["url"] == "http://127.0.0.1:9122/api/sessions/s-1?profile=pam"


def test_list_returns_only_own_rows(client, monkeypatch):
    rows = [{"hermes_session_id": "s-1", "title": "Hi", "created_at": "2026-07-01T00:00:00Z",
             "last_active_at": "2026-07-02T00:00:00Z"}]
    seen = {}

    def fake_rows(agent, user_id):
        seen["args"] = (agent, user_id)
        return rows

    monkeypatch.setattr(sessions, "_list_user_rows", fake_rows)
    r = client.get("/v1/sessions/real-estate", headers={"X-Auth-User-Id": USER_A})
    assert r.status_code == 200
    assert seen["args"] == ("real-estate", USER_A)
    assert r.json() == [{"id": "s-1", "title": "Hi",
                         "createdAt": "2026-07-01T00:00:00Z", "lastActiveAt": "2026-07-02T00:00:00Z"}]


def test_messages_403_for_non_owner(client, monkeypatch):
    monkeypatch.setattr(sessions, "get_session_owner", lambda a, s: USER_A)
    called = []
    monkeypatch.setattr(sessions, "_dashboard_get", lambda a, p: called.append(p) or (200, b"[]"))
    r = client.get("/v1/sessions/real-estate/s-1/messages", headers={"X-Auth-User-Id": USER_B})
    assert r.status_code == 403
    assert r.json() == {"detail": "Session not found"}
    assert called == [], "dashboard must not be touched on ownership failure"


def test_messages_403_for_unknown_session(client, monkeypatch):
    monkeypatch.setattr(sessions, "get_session_owner", lambda a, s: None)
    r = client.get("/v1/sessions/real-estate/s-9/messages", headers={"X-Auth-User-Id": USER_A})
    assert r.status_code == 403


def test_messages_proxies_for_owner(client, monkeypatch):
    monkeypatch.setattr(sessions, "get_session_owner", lambda a, s: USER_A)
    monkeypatch.setattr(sessions, "_dashboard_get",
                        lambda a, p: (200, json.dumps([{"id": 1, "content": "hello"}]).encode()))
    r = client.get("/v1/sessions/real-estate/s-1/messages", headers={"X-Auth-User-Id": USER_A})
    assert r.status_code == 200
    assert r.json() == [{"id": 1, "content": "hello"}]


def test_delete_403_for_non_owner(client, monkeypatch):
    monkeypatch.setattr(sessions, "get_session_owner", lambda a, s: USER_A)
    r = client.delete("/v1/sessions/real-estate/s-1", headers={"X-Auth-User-Id": USER_B})
    assert r.status_code == 403


def test_delete_proxies_and_removes_row_for_owner(client, monkeypatch):
    monkeypatch.setattr(sessions, "get_session_owner", lambda a, s: USER_A)
    dashboard_calls, sb_deletes = [], []
    monkeypatch.setattr(sessions, "_dashboard_delete", lambda a, p: dashboard_calls.append(p) or (200, b"{}"))
    monkeypatch.setattr(sessions, "_delete_row", lambda a, s: sb_deletes.append((a, s)))
    r = client.delete("/v1/sessions/real-estate/s-1", headers={"X-Auth-User-Id": USER_A})
    assert r.status_code == 200
    assert dashboard_calls == ["/api/sessions/s-1"]
    assert sb_deletes == [("real-estate", "s-1")]


def test_delete_removes_row_when_dashboard_returns_404(client, monkeypatch):
    """A 404 from the dashboard means the session is already gone there — the
    ownership row is now pointing at nothing, so it should still be cleaned up."""
    monkeypatch.setattr(sessions, "get_session_owner", lambda a, s: USER_A)
    sb_deletes = []
    monkeypatch.setattr(sessions, "_dashboard_delete", lambda a, p: (404, b'{"detail":"not found"}'))
    monkeypatch.setattr(sessions, "_delete_row", lambda a, s: sb_deletes.append((a, s)))
    r = client.delete("/v1/sessions/real-estate/s-1", headers={"X-Auth-User-Id": USER_A})
    assert r.status_code == 404
    assert sb_deletes == [("real-estate", "s-1")]


def test_delete_keeps_row_when_dashboard_returns_5xx(client, monkeypatch):
    """On a dashboard 5xx we don't know whether the session actually got
    deleted upstream — keep the ownership row so the session isn't left
    orphaned-inaccessible (owner can retry the delete later)."""
    monkeypatch.setattr(sessions, "get_session_owner", lambda a, s: USER_A)
    sb_deletes = []
    monkeypatch.setattr(sessions, "_dashboard_delete", lambda a, p: (500, b'{"detail":"boom"}'))
    monkeypatch.setattr(sessions, "_delete_row", lambda a, s: sb_deletes.append((a, s)))
    r = client.delete("/v1/sessions/real-estate/s-1", headers={"X-Auth-User-Id": USER_A})
    assert r.status_code == 500
    assert sb_deletes == []


def test_touch_session_patches_last_active_at(monkeypatch):
    """touch_session PATCHes the ownership row's last_active_at with a real
    ISO-8601 UTC timestamp (PostgREST needs a literal value, not now())."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-key")
    calls = []

    class FakeResp:
        status_code = 204

        def raise_for_status(self):
            pass

    def fake_patch(url, params=None, headers=None, json=None, timeout=None):
        calls.append((url, params, headers, json))
        return FakeResp()

    monkeypatch.delenv("INSTANCE_ID", raising=False)
    monkeypatch.setattr(sessions.httpx, "patch", fake_patch)
    sessions.touch_session("real-estate", "s-1")

    assert len(calls) == 1
    url, params, headers, body = calls[0]
    assert url == "https://test.supabase.co/rest/v1/agent_sessions"
    assert params == {"agent_id": "eq.real-estate", "hermes_session_id": "eq.s-1",
                      "instance_id": "is.null"}
    assert headers["apikey"] == "svc-key"
    assert headers["Authorization"] == "Bearer svc-key"
    assert "last_active_at" in body
    # A real ISO-8601 timestamp, not the SQL literal "now()".
    assert body["last_active_at"] != "now()"
    assert "T" in body["last_active_at"]


def test_touch_session_never_raises_on_failure(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-key")

    def fake_patch(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(sessions.httpx, "patch", fake_patch)
    sessions.touch_session("real-estate", "s-1")  # must not raise


def test_touch_session_noop_without_supabase_config(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    called = []
    monkeypatch.setattr(sessions.httpx, "patch", lambda *a, **kw: called.append(True))
    sessions.touch_session("real-estate", "s-1")
    assert called == []


def test_record_run_owner_posts_with_on_conflict_and_ignore_duplicates(monkeypatch):
    """record_run_owner POSTs to /rest/v1/run_owners with on_conflict=run_id and
    the ignore-duplicates Prefer header, mirroring record_session."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-key")
    calls = []

    class FakeResp:
        status_code = 201

        def raise_for_status(self):
            pass

    def fake_post(url, params=None, headers=None, json=None, timeout=None):
        calls.append((url, params, headers, json))
        return FakeResp()

    monkeypatch.setattr(sessions.httpx, "post", fake_post)
    sessions.record_run_owner("run-1", USER_A)

    assert len(calls) == 1
    url, params, headers, body = calls[0]
    assert url == "https://test.supabase.co/rest/v1/run_owners"
    assert params == {"on_conflict": "run_id"}
    assert headers["apikey"] == "svc-key"
    assert headers["Authorization"] == "Bearer svc-key"
    assert headers["Prefer"] == "resolution=ignore-duplicates,return=minimal"
    assert body == {"run_id": "run-1", "user_id": USER_A}


def test_record_run_owner_never_raises_on_failure(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-key")

    def fake_post(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(sessions.httpx, "post", fake_post)
    sessions.record_run_owner("run-1", USER_A)  # must not raise


def test_record_run_owner_noop_without_supabase_config(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    called = []
    monkeypatch.setattr(sessions.httpx, "post", lambda *a, **kw: called.append(True))
    sessions.record_run_owner("run-1", USER_A)
    assert called == []


def test_get_run_owner_returns_user_id(monkeypatch):
    """get_run_owner GETs /rest/v1/run_owners filtered by run_id and returns
    the owning user_id, mirroring get_session_owner."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-key")
    calls = []

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return [{"user_id": USER_A}]

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params, headers))
        return FakeResp()

    monkeypatch.setattr(sessions.httpx, "get", fake_get)
    result = sessions.get_run_owner("run-1")

    assert result == USER_A
    assert len(calls) == 1
    url, params, headers = calls[0]
    assert url == "https://test.supabase.co/rest/v1/run_owners"
    assert params == {"run_id": "eq.run-1", "select": "user_id"}
    assert headers["apikey"] == "svc-key"
    assert headers["Authorization"] == "Bearer svc-key"


def test_get_run_owner_returns_none_when_no_rows(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-key")

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return []

    monkeypatch.setattr(sessions.httpx, "get", lambda *a, **kw: FakeResp())
    assert sessions.get_run_owner("run-unknown") is None


def test_get_run_owner_returns_none_on_failure(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-key")

    def fake_get(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(sessions.httpx, "get", fake_get)
    assert sessions.get_run_owner("run-1") is None


def test_get_run_owner_noop_without_supabase_config(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    called = []
    monkeypatch.setattr(sessions.httpx, "get", lambda *a, **kw: called.append(True))
    assert sessions.get_run_owner("run-1") is None
    assert called == []


# --- instance scoping (cross-instance session bleed fix, 2026-07-07) ---------
# agent_sessions is shared per Supabase project; when two boxes shared one
# project, each box's owner-filtered reads returned the OTHER box's rows too
# (list showed foreign session ids; messages proxied to the local Hermes and
# 404'd). Every agent_sessions read/write must therefore scope to this box's
# INSTANCE_ID — eq.<id> when set, is.null when unset (single-box installs whose
# rows were written without an instance tag).


class _JsonResp:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload if payload is not None else []
        self.status_code = status_code

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _sb_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-key")


def test_get_session_owner_scopes_to_instance_when_set(monkeypatch):
    _sb_env(monkeypatch)
    monkeypatch.setenv("INSTANCE_ID", "sandbox")
    calls = []
    monkeypatch.setattr(sessions.httpx, "get",
                        lambda url, params=None, headers=None, timeout=None:
                        calls.append(params) or _JsonResp([{"user_id": USER_A}]))
    assert sessions.get_session_owner("real-estate", "s-1") == USER_A
    assert calls[0]["instance_id"] == "eq.sandbox"


def test_get_session_owner_scopes_to_null_instance_when_unset(monkeypatch):
    _sb_env(monkeypatch)
    monkeypatch.delenv("INSTANCE_ID", raising=False)
    calls = []
    monkeypatch.setattr(sessions.httpx, "get",
                        lambda url, params=None, headers=None, timeout=None:
                        calls.append(params) or _JsonResp([]))
    sessions.get_session_owner("real-estate", "s-1")
    assert calls[0]["instance_id"] == "is.null"


def test_record_session_stamps_instance_id_when_set(monkeypatch):
    _sb_env(monkeypatch)
    monkeypatch.setenv("INSTANCE_ID", "sandbox")
    calls = []
    monkeypatch.setattr(sessions.httpx, "post",
                        lambda url, params=None, headers=None, json=None, timeout=None:
                        calls.append(json) or _JsonResp(status_code=201))
    sessions.record_session("real-estate", "s-1", USER_A)
    assert calls[0]["instance_id"] == "sandbox"


def test_record_session_omits_instance_id_when_unset(monkeypatch):
    _sb_env(monkeypatch)
    monkeypatch.delenv("INSTANCE_ID", raising=False)
    calls = []
    monkeypatch.setattr(sessions.httpx, "post",
                        lambda url, params=None, headers=None, json=None, timeout=None:
                        calls.append(json) or _JsonResp(status_code=201))
    sessions.record_session("real-estate", "s-1", USER_A)
    assert "instance_id" not in calls[0]


def test_list_user_rows_scopes_to_instance(monkeypatch):
    _sb_env(monkeypatch)
    monkeypatch.setenv("INSTANCE_ID", "sandbox")
    calls = []
    monkeypatch.setattr(sessions.httpx, "get",
                        lambda url, params=None, headers=None, timeout=None:
                        calls.append(params) or _JsonResp([]))
    sessions._list_user_rows("real-estate", USER_A)
    assert calls[0]["instance_id"] == "eq.sandbox"


def test_touch_session_scopes_to_instance(monkeypatch):
    _sb_env(monkeypatch)
    monkeypatch.setenv("INSTANCE_ID", "sandbox")
    calls = []
    monkeypatch.setattr(sessions.httpx, "patch",
                        lambda url, params=None, headers=None, json=None, timeout=None:
                        calls.append(params) or _JsonResp(status_code=204))
    sessions.touch_session("real-estate", "s-1")
    assert calls[0]["instance_id"] == "eq.sandbox"


def test_delete_row_scopes_to_instance(monkeypatch):
    _sb_env(monkeypatch)
    monkeypatch.setenv("INSTANCE_ID", "sandbox")
    calls = []
    monkeypatch.setattr(sessions.httpx, "delete",
                        lambda url, params=None, headers=None, timeout=None:
                        calls.append(params) or _JsonResp(status_code=204))
    sessions._delete_row("real-estate", "s-1")
    assert calls[0]["instance_id"] == "eq.sandbox"
