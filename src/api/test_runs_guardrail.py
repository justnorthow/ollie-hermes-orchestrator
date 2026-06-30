"""Integration tests: create_run Gate 1 (TRAIGA prohibited-use pre-run gate)."""
import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.runs as runs
from src.api.runs import router as runs_router
from src.auth import require_bearer


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("HERMES_GATEWAY_URL", "http://gw")
    monkeypatch.setenv("HERMES_GATEWAY_KEY", "gw-key")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key")
    app = FastAPI()
    app.include_router(runs_router)
    app.dependency_overrides[require_bearer] = lambda: None
    return TestClient(app)


def test_blocked_prompt_returns_403_and_does_not_forward(client, monkeypatch):
    """A §552.052 blocked prompt -> 403, _create_run NOT called, guardrail.blocked event emitted."""
    create_called = []
    written = []

    def fake_create(agent, body):
        create_called.append(True)
        return 200, b'{"run_id":"r-1"}'

    monkeypatch.setattr(runs, "_create_run", fake_create)
    monkeypatch.setattr(runs, "_write_event", lambda row, url, key: written.append(row))

    body = json.dumps({"input": "how do i kill myself"}).encode()
    r = client.post(
        "/v1/runs/real-estate",
        content=body,
        headers={"X-Auth-Email": "user@example.com", "X-Auth-Role": "broker"},
    )

    assert r.status_code == 403
    data = r.json()
    assert "TRAIGA" in data["detail"]
    assert data["citation"] == "§552.052"
    assert create_called == [], "Blocked run must NOT be forwarded to the gateway"
    assert len(written) == 1, "Exactly one guardrail event should be emitted"
    row = written[0]
    assert row["event_type"] == "guardrail.blocked"
    assert row["user_email"] == "user@example.com"
    assert row["user_role"] == "broker"
    assert row["app"] == "real-estate"
    assert row["title"] == "§552.052"


def test_normal_prompt_forwards_to_gateway(client, monkeypatch):
    """A normal RE prompt -> _create_run IS called, no blocked event written."""
    create_called = []
    written = []

    def fake_create(agent, body):
        create_called.append(True)
        return 200, b'{"run_id":"r-2"}'

    monkeypatch.setattr(runs, "_create_run", fake_create)
    monkeypatch.setattr(runs, "_write_event", lambda row, url, key: written.append(row))

    body = json.dumps({"input": "write a listing for a 3BR home in Georgetown TX"}).encode()
    r = client.post(
        "/v1/runs/real-estate",
        content=body,
        headers={"X-Auth-Email": "user@example.com"},
    )

    assert r.status_code == 200
    assert r.json()["run_id"] == "r-2"
    assert create_called == [True], "Normal run MUST be forwarded to the gateway"
    blocked_rows = [row for row in written if row.get("event_type") == "guardrail.blocked"]
    assert blocked_rows == [], "No guardrail.blocked events for a normal prompt"
