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


def test_governed_compliance_screen_run_enforces_and_captures(client, monkeypatch):
    """C v1.1: a governed compliance_screen run now passes through the attestation gate
    (enforcement record) AND writes the rich extractor capture — two rows, one delivered frame.
    (Previously this streamed through unchanged and wrote only the capture row.)"""
    written = []
    async def fake_stream(base, run_id):
        yield sse('Body.\n\n```compliance\nSTATUS: PASS\n```')
    monkeypatch.setattr(runs, "_stream_upstream", fake_stream)
    monkeypatch.setattr(runs, "_write_event", lambda row, url, key: written.append(row))
    monkeypatch.delenv("GUARDRAIL_ENFORCE_APPS", raising=False)
    r = client.get(
        "/v1/runs/real-estate/r-1/events",
        headers={"X-Auth-Email": "a@b.com", "X-Auth-Role": "agent",
                 "X-Gov-App": "newsletter", "X-Gov-Event-Type": "compliance_screen",
                 "X-Gov-Title": "Field%20Notes"},
    )
    assert r.status_code == 200
    assert b"run.completed" in r.content
    by_type = {w["event_type"]: w for w in written}
    # Enforcement record (attestation gate) — no attestation present ⇒ unattested (observe).
    assert "attestation.unattested" in by_type
    # Rich capture row (extractor) retains the SP1 contract.
    cap = by_type["compliance_screen"]
    assert cap["user_email"] == "a@b.com"
    assert cap["app"] == "newsletter"
    assert cap["status"] == "pass"
    assert cap["title"] == "Field Notes"             # URL-decoded
    assert cap["run_id"] == "r-1"


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
    monkeypatch.delenv("HERMES_GATEWAY_URLS", raising=False)
    app = FastAPI()
    app.include_router(runs_router)
    app.dependency_overrides[require_bearer] = lambda: None
    r = TestClient(app).post("/v1/runs/real-estate", content=b'{"input":"go"}')
    assert r.status_code == 503


def test_503_get_events_without_gateway_env(monkeypatch):
    monkeypatch.delenv("HERMES_GATEWAY_URL", raising=False)
    monkeypatch.delenv("HERMES_GATEWAY_URLS", raising=False)
    app = FastAPI()
    app.include_router(runs_router)
    app.dependency_overrides[require_bearer] = lambda: None
    r = TestClient(app).get("/v1/runs/real-estate/r-1/events")
    assert r.status_code == 503


def test_gateway_base_resolves_per_agent_without_appending(monkeypatch):
    # Each agent has its own gateway (distinct host:port) — resolve per-agent,
    # do NOT append /{agent}. Trailing slash is stripped.
    monkeypatch.setenv("HERMES_GATEWAY_URLS",
                       '{"real-estate":"http://127.0.0.1:8644/","other":"http://127.0.0.1:8645"}')
    monkeypatch.delenv("HERMES_GATEWAY_URL", raising=False)
    assert runs._gateway_base("real-estate") == "http://127.0.0.1:8644"
    assert runs._gateway_base("other") == "http://127.0.0.1:8645"
    assert runs._gateway_base("unknown") is None


def test_gateway_base_falls_back_to_single_url(monkeypatch):
    monkeypatch.delenv("HERMES_GATEWAY_URLS", raising=False)
    monkeypatch.setenv("HERMES_GATEWAY_URL", "http://gw/")
    assert runs._gateway_base("real-estate") == "http://gw"
