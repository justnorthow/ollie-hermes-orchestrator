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
