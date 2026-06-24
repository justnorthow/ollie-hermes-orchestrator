import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.runs as runs
from src.api.runs import router as runs_router, _extract_output
from src.auth import require_bearer

URL = "https://core.supabase.co"
KEY = "service-role-key"


def sse(output: str) -> bytes:
    return (
        b'data: {"event":"message.delta","delta":"hi"}\n\n'
        + b'data: ' + json.dumps({"event": "run.completed", "output": output}).encode() + b'\n\n'
    )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("HERMES_GATEWAY_URL", "http://gw")
    monkeypatch.setenv("HERMES_GATEWAY_KEY", "gw-key")
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
    app = FastAPI()
    app.include_router(runs_router)
    app.dependency_overrides[require_bearer] = lambda: None
    return TestClient(app)


def test_extract_output_finds_completed():
    buf = sse("FINAL OUTPUT").decode()
    assert _extract_output(buf) == "FINAL OUTPUT"
    assert _extract_output('data: {"event":"message.delta","delta":"x"}\n\n') is None


def test_create_forwards_and_returns_run_id(client, monkeypatch):
    seen = {}
    def fake(agent, body):
        seen.update(agent=agent, body=body)
        return 200, b'{"run_id":"r-1"}'
    monkeypatch.setattr(runs, "_create_run", fake)
    r = client.post("/v1/runs/real-estate", content=b'{"input":"go"}')
    assert r.status_code == 200
    assert r.json()["run_id"] == "r-1"
    assert seen["agent"] == "real-estate"


def test_events_streams_through_and_writes_one_row_on_governed_run(client, monkeypatch):
    written = []
    async def fake_stream(base, run_id):
        yield sse('Body.\n\n```compliance\nSTATUS: PASS\n```')
    monkeypatch.setattr(runs, "_stream_upstream", fake_stream)
    monkeypatch.setattr(runs, "_write_event", lambda row, url, key: written.append(row))
    r = client.get(
        "/v1/runs/real-estate/r-1/events",
        headers={"X-Auth-Email": "a@b.com", "X-Auth-Role": "agent",
                 "X-Gov-App": "newsletter", "X-Gov-Event-Type": "compliance_screen",
                 "X-Gov-Title": "Field%20Notes"},
    )
    assert r.status_code == 200
    assert b"run.completed" in r.content              # streamed through unchanged
    assert len(written) == 1
    row = written[0]
    assert row["user_email"] == "a@b.com"
    assert row["app"] == "newsletter"
    assert row["status"] == "pass"
    assert row["title"] == "Field Notes"             # URL-decoded
    assert row["run_id"] == "r-1"


def test_events_writes_nothing_without_gov_headers(client, monkeypatch):
    written = []
    async def fake_stream(base, run_id):
        yield sse('Body.\n\n```compliance\nSTATUS: PASS\n```')
    monkeypatch.setattr(runs, "_stream_upstream", fake_stream)
    monkeypatch.setattr(runs, "_write_event", lambda row, url, key: written.append(row))
    r = client.get("/v1/runs/real-estate/r-1/events", headers={"X-Auth-Email": "a@b.com"})
    assert r.status_code == 200
    assert written == []                              # no X-Gov-* ⇒ not governed


def test_events_capture_error_does_not_break_stream(client, monkeypatch):
    async def fake_stream(base, run_id):
        yield sse('Body.\n\n```compliance\nSTATUS: PASS\n```')
    def boom(row, url, key):
        raise RuntimeError("supabase down")
    monkeypatch.setattr(runs, "_stream_upstream", fake_stream)
    monkeypatch.setattr(runs, "_write_event", boom)
    r = client.get(
        "/v1/runs/real-estate/r-1/events",
        headers={"X-Auth-Email": "a@b.com", "X-Gov-App": "newsletter",
                 "X-Gov-Event-Type": "compliance_screen"},
    )
    assert r.status_code == 200
    assert b"run.completed" in r.content              # stream still delivered


def test_503_without_gateway_env(monkeypatch):
    monkeypatch.delenv("HERMES_GATEWAY_URL", raising=False)
    app = FastAPI()
    app.include_router(runs_router)
    app.dependency_overrides[require_bearer] = lambda: None
    r = TestClient(app).post("/v1/runs/real-estate", content=b'{"input":"go"}')
    assert r.status_code == 503


def test_503_get_events_without_gateway_env(monkeypatch):
    monkeypatch.delenv("HERMES_GATEWAY_URL", raising=False)
    app = FastAPI()
    app.include_router(runs_router)
    app.dependency_overrides[require_bearer] = lambda: None
    r = TestClient(app).get("/v1/runs/real-estate/r-1/events")
    assert r.status_code == 503
