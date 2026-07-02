"""Integration tests: TRAIGA gates in run_events and create_run.

Gate 1 (create_run pre-run): prohibited-use refusal via screen_input.
Gate 2 (run_events post-run): attestation gate via parse/strip/decide_attestation.
"""
import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.runs as runs
from src.api.runs import router as runs_router
from src.auth import require_bearer


# ---------------------------------------------------------------------------
# Helpers shared by Gate 2 tests
# ---------------------------------------------------------------------------

def _sse(output: str) -> bytes:
    """Build a minimal two-frame SSE byte sequence ending with run.completed."""
    return (
        b'data: {"event":"message.delta","delta":"hi"}\n\n'
        + b"data: " + json.dumps({"event": "run.completed", "output": output}).encode() + b"\n\n"
    )


_ATT_PASS_BLOCK = (
    '<!--JNOW-COMPLIANCE-ATTESTATION\n'
    '{"screened":"pass","rules":["fha-general"],"skill":"newsletter","v":1}\n'
    '-->'
)


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


# ---------------------------------------------------------------------------
# Gate 2 — run_events post-run attestation gate
# ---------------------------------------------------------------------------

def test_governed_pass_attestation_strips_comment_and_captures(client, monkeypatch):
    """(a) Governed run + pass attestation: comment stripped from delivery, attestation.pass row captured."""
    written: list[dict] = []
    output_with_att = f"Listing copy here.\n{_ATT_PASS_BLOCK}"

    async def fake_stream(base, run_id):
        yield _sse(output_with_att)

    monkeypatch.setattr(runs, "_stream_upstream", fake_stream)
    monkeypatch.setattr(runs, "_write_event", lambda row, url, key: written.append(row))
    monkeypatch.delenv("GUARDRAIL_ENFORCE_APPS", raising=False)

    r = client.get(
        "/v1/runs/real-estate/r-1/events",
        headers={
            "X-Auth-Email": "broker@example.com",
            "X-Auth-Role": "broker",
            "X-Gov-App": "real-estate",
        },
    )

    assert r.status_code == 200
    assert len(written) == 1
    row = written[0]
    assert row["event_type"] == "attestation.pass"
    assert row["app"] == "real-estate"
    assert row["findings"] == ["fha-general"]
    assert row["run_id"] == "r-1"
    assert row["user_email"] == "broker@example.com"
    # Attestation comment must NOT appear in the client-facing output.
    content = r.content.decode()
    assert "JNOW-COMPLIANCE-ATTESTATION" not in content
    assert "Listing copy here." in content


def test_governed_no_attestation_observe_mode_delivers(client, monkeypatch):
    """(b) Governed run + no attestation, app NOT in GUARDRAIL_ENFORCE_APPS (observe): output
    delivered, attestation.unattested row captured."""
    written: list[dict] = []

    async def fake_stream(base, run_id):
        yield _sse("Plain listing copy here.")

    monkeypatch.setattr(runs, "_stream_upstream", fake_stream)
    monkeypatch.setattr(runs, "_write_event", lambda row, url, key: written.append(row))
    monkeypatch.delenv("GUARDRAIL_ENFORCE_APPS", raising=False)

    r = client.get(
        "/v1/runs/real-estate/r-1/events",
        headers={
            "X-Auth-Email": "broker@example.com",
            "X-Auth-Role": "broker",
            "X-Gov-App": "real-estate",
        },
    )

    assert r.status_code == 200
    assert len(written) == 1
    row = written[0]
    assert row["event_type"] == "attestation.unattested"
    assert row["app"] == "real-estate"
    assert "Plain listing copy here." in r.content.decode()


def test_governed_no_attestation_enforce_mode_withholds(client, monkeypatch):
    """(c) Governed run + no attestation, app IN GUARDRAIL_ENFORCE_APPS (enforce): output held,
    attestation.withheld row captured."""
    written: list[dict] = []

    async def fake_stream(base, run_id):
        yield _sse("Plain listing copy here.")

    monkeypatch.setattr(runs, "_stream_upstream", fake_stream)
    monkeypatch.setattr(runs, "_write_event", lambda row, url, key: written.append(row))
    monkeypatch.setenv("GUARDRAIL_ENFORCE_APPS", "real-estate,newsletter")

    r = client.get(
        "/v1/runs/real-estate/r-1/events",
        headers={
            "X-Auth-Email": "broker@example.com",
            "X-Auth-Role": "broker",
            "X-Gov-App": "real-estate",
        },
    )

    assert r.status_code == 200
    assert len(written) == 1
    row = written[0]
    assert row["event_type"] == "attestation.withheld"
    assert row["app"] == "real-estate"
    content = r.content.decode()
    assert "Held for compliance review." in content
    assert "Plain listing copy here." not in content


def test_non_governed_streams_unchanged_no_attestation_row(client, monkeypatch):
    """(d) Non-governed run (no X-Gov-App): streams byte-for-byte unchanged, no attestation row."""
    written: list[dict] = []
    original_sse = _sse("Plain output here.")

    async def fake_stream(base, run_id):
        yield original_sse

    monkeypatch.setattr(runs, "_stream_upstream", fake_stream)
    monkeypatch.setattr(runs, "_write_event", lambda row, url, key: written.append(row))

    r = client.get(
        "/v1/runs/real-estate/r-1/events",
        headers={"X-Auth-Email": "broker@example.com"},
        # No X-Gov-App header — non-governed.
    )

    assert r.status_code == 200
    # No attestation events written.
    attestation_rows = [w for w in written if "attestation" in w.get("event_type", "")]
    assert attestation_rows == [], "Non-governed run must not produce attestation events"
    # Original SSE bytes passed through unchanged.
    assert b"run.completed" in r.content
    assert b"Plain output here." in r.content
