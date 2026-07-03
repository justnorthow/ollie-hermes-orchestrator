"""Run-control passthrough routes (Phase 0 — closes the browser->gateway bypass)."""
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
    runs._RUN_OWNERS.clear()
    app = FastAPI()
    app.include_router(runs_router)
    app.dependency_overrides[require_bearer] = lambda: None
    return TestClient(app)


def test_stop_forwards(client, monkeypatch):
    calls = []
    monkeypatch.setattr(runs, "_gateway_post",
                        lambda agent, path, body=b"": calls.append((agent, path)) or (200, b"{}"))
    r = client.post("/v1/runs/real-estate/r1/stop", headers={"X-Auth-User-Id": USER_A})
    assert r.status_code == 200
    assert calls == [("real-estate", "/v1/runs/r1/stop")]


def test_stop_403_for_foreign_run(client, monkeypatch):
    runs._RUN_OWNERS["r1"] = USER_A
    monkeypatch.setattr(runs, "_gateway_post", lambda agent, path, body=b"": (200, b"{}"))
    r = client.post("/v1/runs/real-estate/r1/stop", headers={"X-Auth-User-Id": USER_B})
    assert r.status_code == 403


def test_approval_forwards_body(client, monkeypatch):
    calls = []

    def fake_post(agent, path, body=b""):
        calls.append((path, json.loads(body)))
        return 200, b"{}"

    monkeypatch.setattr(runs, "_gateway_post", fake_post)
    r = client.post("/v1/runs/real-estate/r1/approval",
                    content=json.dumps({"approved": True}).encode(),
                    headers={"X-Auth-User-Id": USER_A, "content-type": "application/json"})
    assert r.status_code == 200
    assert calls == [("/v1/runs/r1/approval", {"approved": True})]


def test_list_runs_forwards_query(client, monkeypatch):
    calls = []
    monkeypatch.setattr(runs, "_gateway_get", lambda agent, path: calls.append(path) or (200, b"[]"))
    r = client.get("/v1/runs/real-estate?status=pending_approval", headers={"X-Auth-User-Id": USER_A})
    assert r.status_code == 200
    assert calls == ["/v1/runs?status=pending_approval"]
