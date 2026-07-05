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


def test_events_403_for_unknown_run_owner(client, monkeypatch):
    async def fake_stream(base, run_id):
        yield b'data: {"event":"message.delta","delta":"hi"}\n\n'

    monkeypatch.setattr(runs, "_stream_upstream", fake_stream)
    r = client.get("/v1/runs/real-estate/r-unknown/events", headers={"X-Auth-User-Id": USER_A})
    assert r.status_code == 403


def test_touch_session_called_on_owned_session_continue(client, monkeypatch):
    monkeypatch.setattr(runs, "_create_run", lambda a, b: (200, b'{"run_id":"r1"}'))
    monkeypatch.setattr(runs, "_session_owner", lambda a, s: USER_A)
    touched = []
    monkeypatch.setattr(runs._sessions_store, "touch_session", lambda a, s: touched.append((a, s)))
    r = _post_run(client, session_id="s-1", user=USER_A)
    assert r.status_code == 200
    assert touched == [("real-estate", "s-1")]


def test_touch_session_not_called_when_no_session_id(client, monkeypatch):
    monkeypatch.setattr(runs, "_create_run", lambda a, b: (200, b'{"run_id":"r1"}'))
    touched = []
    monkeypatch.setattr(runs._sessions_store, "touch_session", lambda a, s: touched.append((a, s)))
    r = _post_run(client, session_id=None, user=USER_A)
    assert r.status_code == 200
    assert touched == []


def test_touch_session_not_called_when_no_user_id(client, monkeypatch):
    monkeypatch.setattr(runs, "_create_run", lambda a, b: (200, b'{"run_id":"r1"}'))
    monkeypatch.setattr(runs, "_session_owner", lambda a, s: USER_B)
    touched = []
    monkeypatch.setattr(runs._sessions_store, "touch_session", lambda a, s: touched.append((a, s)))
    r = _post_run(client, session_id="s-1", user=None)
    assert r.status_code == 200
    assert touched == []


def test_touch_session_not_called_when_ownership_check_fails(client, monkeypatch):
    called = []
    monkeypatch.setattr(runs, "_create_run", lambda a, b: called.append(True) or (200, b'{"run_id":"r1"}'))
    monkeypatch.setattr(runs, "_session_owner", lambda a, s: USER_B)
    touched = []
    monkeypatch.setattr(runs._sessions_store, "touch_session", lambda a, s: touched.append((a, s)))
    r = _post_run(client, session_id="s-1", user=USER_A)
    assert r.status_code == 403
    assert touched == []
    assert called == []


def test_identity_header_skew_warns_once_per_process(client, monkeypatch, caplog):
    """X-Auth-Email present without X-Auth-User-Id signals stale nginx or an old
    validator silently skipping ownership checks — this must be grep-able, but
    only once per process to avoid log spam."""
    monkeypatch.setattr(runs, "_create_run", lambda a, b: (200, b'{"run_id":"r1"}'))
    runs._IDENTITY_SKEW_WARNED = False
    body = json.dumps({"input": "hi"}).encode()
    headers = {"X-Auth-Email": "a@x.com", "X-Auth-Role": "agent"}  # no X-Auth-User-Id

    with caplog.at_level("WARNING", logger="src.api.runs"):
        r1 = client.post("/v1/runs/real-estate", content=body, headers=headers)
        r2 = client.post("/v1/runs/real-estate", content=body, headers=headers)

    assert r1.status_code == 200
    assert r2.status_code == 200
    skew_msgs = [m for m in caplog.messages if "identity header skew" in m]
    assert len(skew_msgs) == 1


def test_identity_header_skew_not_warned_when_user_id_present(client, monkeypatch, caplog):
    monkeypatch.setattr(runs, "_create_run", lambda a, b: (200, b'{"run_id":"r1"}'))
    runs._IDENTITY_SKEW_WARNED = False
    r = _post_run(client, user=USER_A)
    with caplog.at_level("WARNING", logger="src.api.runs"):
        pass
    skew_msgs = [m for m in caplog.messages if "identity header skew" in m]
    assert skew_msgs == []


def test_large_run_completed_frame_still_captures_session(client, monkeypatch):
    """A run.completed data line bigger than the old 64KB rolling-tail cap must
    still be parsed and recorded — this is the bug that used to truncate the
    JSON and silently strand the session (creator's next message 403s)."""
    monkeypatch.setattr(runs, "_create_run", lambda a, b: (200, b'{"run_id":"r-big"}'))
    recorded = []
    monkeypatch.setattr(runs, "_record_session", lambda a, s, u: recorded.append((a, s, u)))
    r = _post_run(client, user=USER_A)
    assert r.status_code == 200

    big_output = "x" * 200_000  # comfortably bigger than the old 65536-byte cap
    frame = ('data: ' + json.dumps(
        {"event": "run.completed", "output": big_output, "session_id": "s-big"}
    ) + "\n\n").encode()

    async def fake_stream(base, run_id):
        # Deliver the oversized frame split across several chunks, as a real
        # HTTP stream would, rather than as one atomic yield.
        step = 4096
        for i in range(0, len(frame), step):
            yield frame[i:i + step]

    monkeypatch.setattr(runs, "_stream_upstream", fake_stream)
    r2 = client.get("/v1/runs/real-estate/r-big/events", headers={"X-Auth-User-Id": USER_A})
    assert r2.status_code == 200
    assert r2.content == frame  # delivered bytes are byte-identical to upstream
    assert recorded == [("real-estate", "s-big", USER_A)]


def test_session_captured_before_client_disconnect_drains_stream(client, monkeypatch):
    """If the client stops consuming the response body right after the
    session_id-bearing chunk arrives (simulating an early disconnect), the
    session must already be recorded -- capture cannot be deferred to code
    that only runs after the async generator fully drains.

    Drives runs.run_events' inner generator directly (rather than through
    TestClient) so we can stop consuming after the second chunk and inspect
    state exactly as a real client disconnect would leave it -- TestClient
    always fully drains the StreamingResponse body, which would not exercise
    the disconnect path at all.
    """
    monkeypatch.setenv("HERMES_GATEWAY_URL", "http://gw")
    monkeypatch.setattr(runs, "_create_run", lambda a, b: (200, b'{"run_id":"r-disc"}'))
    recorded = []
    monkeypatch.setattr(runs, "_record_session", lambda a, s, u: recorded.append((a, s, u)))
    runs._RUN_OWNERS["r-disc"] = USER_A

    frame1 = b'data: {"event":"message.delta","delta":"hi"}\n\n'
    frame2 = (
        'data: ' + json.dumps({"event": "run.completed", "output": "done", "session_id": "s-disc"}) + '\n\n'
    ).encode()
    more_frames_yielded = []

    async def fake_stream(base, run_id):
        yield frame1
        yield frame2
        # If the generator were cancelled by a real disconnect right here,
        # nothing below this point would ever execute. We simulate that by
        # simply never letting the test's consumer ask for more --- but to
        # prove the implementation doesn't rely on reaching this point,
        # track whether it's reached at all.
        more_frames_yielded.append(True)
        yield b'data: {"event":"noise"}\n\n'

    monkeypatch.setattr(runs, "_stream_upstream", fake_stream)

    import asyncio
    from starlette.requests import Request as StarletteRequest

    scope = {
        "type": "http", "method": "GET", "path": "/v1/runs/real-estate/r-disc/events",
        "headers": [(b"x-auth-user-id", USER_A.encode())], "query_string": b"",
    }

    async def receive():
        return {"type": "http.request", "body": b""}

    request = StarletteRequest(scope, receive)

    async def drive():
        response = await runs.run_events("real-estate", "r-disc", request)
        agen = response.body_iterator
        got = []
        got.append(await agen.__anext__())
        got.append(await agen.__anext__())
        # Simulate an early disconnect: stop consuming and close the
        # generator, exactly like Starlette does when the client goes away.
        await agen.aclose()
        return got

    got = asyncio.run(drive())
    assert got == [frame1, frame2]
    assert more_frames_yielded == [], "test should stop consuming before the 3rd chunk"
    assert recorded == [("real-estate", "s-disc", USER_A)]


def test_gate_falls_back_to_persisted_owner_after_restart(client, monkeypatch):
    """Simulates an orchestrator restart: the in-memory _RUN_OWNERS cache is
    empty, but the run_owners row survived in Supabase. The gate must consult
    _sessions_store.get_run_owner on a memory miss and repopulate the cache --
    the real owner keeps access, a different user still gets 403."""
    monkeypatch.setattr(runs._sessions_store, "get_run_owner",
                        lambda run_id: USER_A if run_id == "r-restart" else None)
    runs._RUN_OWNERS.clear()  # simulate restart: no in-memory cache

    monkeypatch.setattr(runs, "_gateway_post", lambda agent, path, body=b"": (200, b"{}"))
    r_owner = client.post("/v1/runs/real-estate/r-restart/stop", headers={"X-Auth-User-Id": USER_A})
    assert r_owner.status_code == 200
    assert runs._RUN_OWNERS["r-restart"] == USER_A  # cache repopulated

    r_other = client.post("/v1/runs/real-estate/r-restart/stop", headers={"X-Auth-User-Id": USER_B})
    assert r_other.status_code == 403


def test_governed_path_records_at_most_once_and_bytes_unchanged(client, monkeypatch):
    """Governed branch: confirm the existing scan-after-buffering approach
    still records at most once and does not alter delivered bytes."""
    monkeypatch.setattr(runs, "_create_run", lambda a, b: (200, b'{"run_id":"r-gov"}'))
    recorded = []
    monkeypatch.setattr(runs, "_record_session", lambda a, s, u: recorded.append((a, s, u)))
    r = _post_run(client, user=USER_A)
    assert r.status_code == 200

    async def fake_stream(base, run_id):
        yield b'data: {"event":"message.delta","delta":"hi","session_id":"s-gov"}\n\n'
        yield ('data: ' + json.dumps(
            {"event": "run.completed", "output": "ATTESTATION_OK done", "session_id": "s-gov"}
        ) + '\n\n').encode()

    monkeypatch.setattr(runs, "_stream_upstream", fake_stream)
    monkeypatch.setattr(runs, "parse_attestation", lambda out: None)
    monkeypatch.setattr(runs, "strip_attestation", lambda out: out)
    monkeypatch.setattr(runs, "decide_attestation", lambda att, enforce: {"action": "deliver", "event_type": "x"})
    r2 = client.get(
        "/v1/runs/real-estate/r-gov/events",
        headers={"X-Auth-User-Id": USER_A, "X-Gov-App": "newsletter"},
    )
    assert r2.status_code == 200
    assert recorded == [("real-estate", "s-gov", USER_A)]
