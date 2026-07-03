"""Session-ownership enforcement in the run-proxy (Phase 1)."""
import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.runs as runs
from src.api.runs import router as runs_router
from src.auth import require_bearer

USER_A = "aaaaaaaa-0000-0000-0000-000000000001"
USER_B = "bbbbbbbb-0000-0000-0000-000000000002"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("HERMES_GATEWAY_URL", "http://gw")
    monkeypatch.setenv("HERMES_GATEWAY_KEY", "gw-key")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key")
    runs._RUN_OWNERS.clear()
    app = FastAPI()
    app.include_router(runs_router)
    app.dependency_overrides[require_bearer] = lambda: None
    return TestClient(app)


def _post_run(client, session_id=None, user=USER_A):
    body = {"input": "hello there"}
    if session_id:
        body["session_id"] = session_id
    headers = {"X-Auth-Email": "a@x.com", "X-Auth-Role": "agent"}
    if user:
        headers["X-Auth-User-Id"] = user
    return client.post("/v1/runs/real-estate", content=json.dumps(body).encode(), headers=headers)


def test_foreign_session_403_before_gateway(client, monkeypatch):
    called = []
    monkeypatch.setattr(runs, "_create_run", lambda a, b: called.append(True) or (200, b'{"run_id":"r1"}'))
    monkeypatch.setattr(runs, "_session_owner", lambda a, s: USER_B)
    r = _post_run(client, session_id="s-1", user=USER_A)
    assert r.status_code == 403
    assert r.json() == {"detail": "Session not found"}
    assert called == []


def test_unknown_session_403(client, monkeypatch):
    monkeypatch.setattr(runs, "_create_run", lambda a, b: (200, b'{"run_id":"r1"}'))
    monkeypatch.setattr(runs, "_session_owner", lambda a, s: None)
    r = _post_run(client, session_id="s-ghost", user=USER_A)
    assert r.status_code == 403


def test_own_session_forwards(client, monkeypatch):
    monkeypatch.setattr(runs, "_create_run", lambda a, b: (200, b'{"run_id":"r1"}'))
    monkeypatch.setattr(runs, "_session_owner", lambda a, s: USER_A)
    r = _post_run(client, session_id="s-1", user=USER_A)
    assert r.status_code == 200
    assert runs._RUN_OWNERS["r1"] == USER_A


def test_no_identity_skips_check(client, monkeypatch):
    """Internal bearer-authed callers (no X-Auth-User-Id) are inside the trust boundary."""
    monkeypatch.setattr(runs, "_create_run", lambda a, b: (200, b'{"run_id":"r1"}'))
    monkeypatch.setattr(runs, "_session_owner", lambda a, s: USER_B)
    r = _post_run(client, session_id="s-1", user=None)
    assert r.status_code == 200


def test_new_session_recorded_from_stream(client, monkeypatch):
    monkeypatch.setattr(runs, "_create_run", lambda a, b: (200, b'{"run_id":"r7"}'))
    recorded = []
    monkeypatch.setattr(runs, "_record_session", lambda a, s, u: recorded.append((a, s, u)))
    r = _post_run(client, user=USER_A)  # no session_id -> new session
    assert r.status_code == 200

    async def fake_stream(base, run_id):
        yield b'data: {"event":"message.delta","delta":"hi"}\n\n'
        yield b'data: {"event":"run.completed","output":"done","session_id":"s-new"}\n\n'

    monkeypatch.setattr(runs, "_stream_upstream", fake_stream)
    r2 = client.get("/v1/runs/real-estate/r7/events", headers={"X-Auth-User-Id": USER_A})
    assert r2.status_code == 200
    assert recorded == [("real-estate", "s-new", USER_A)]


def test_events_403_for_foreign_run(client, monkeypatch):
    runs._RUN_OWNERS["r9"] = USER_A
    r = client.get("/v1/runs/real-estate/r9/events", headers={"X-Auth-User-Id": USER_B})
    assert r.status_code == 403
