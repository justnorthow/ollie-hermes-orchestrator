import json
import anyio
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(fake_env):
    from src.api.main import create_app
    app = create_app()
    return TestClient(app)


def _auth():
    return {"Authorization": "Bearer topsecret"}


def test_list_agents_starts_empty(client):
    r = client.get("/v1/agents", headers=_auth())
    assert r.status_code == 200
    assert r.json() == {"agents": []}


def test_create_then_list(client):
    body = {
        "name": "paige", "provider": "anthropic", "model": "claude-sonnet-4.6",
        "apiKey": "sk-x", "displayName": "Paige", "color": "#aabbcc",
        "enabledSkills": [],
    }
    r = client.post("/v1/agents", json=body, headers=_auth())
    assert r.status_code == 202
    events = []
    for raw in r.iter_lines():
        if isinstance(raw, bytes):
            raw = raw.decode()
        if raw.startswith("data: "):
            events.append(json.loads(raw[6:]))
    assert any(ev.get("event") == "done" for ev in events)
    r2 = client.get("/v1/agents", headers=_auth())
    assert r2.status_code == 200
    ids = [a["id"] for a in r2.json()["agents"]]
    assert "paige" in ids


def test_delete_round_trips(client):
    body = {"name": "tmp", "provider": "anthropic", "model": "m",
            "apiKey": "k", "enabledSkills": []}
    r = client.post("/v1/agents", json=body, headers=_auth())
    list(r.iter_lines())  # drain
    r2 = client.delete("/v1/agents/tmp", headers=_auth())
    assert r2.status_code == 204
    r3 = client.get("/v1/agents/tmp", headers=_auth())
    assert r3.status_code == 404


def test_unauthenticated_returns_401(client):
    assert client.get("/v1/agents").status_code == 401


def _create_tmp_agent(client):
    body = {"name": "tmp", "provider": "anthropic", "model": "m",
            "apiKey": "k", "enabledSkills": []}
    r = client.post("/v1/agents", json=body, headers=_auth())
    list(r.iter_lines())  # drain the SSE stream so the create completes


def test_delete_schedules_deferred_dashboard_bounce(client, monkeypatch):
    """The bounce runs as a BackgroundTask after the 204 is sent (mirrors
    instance.py's deferred _bounce_after_write) — TestClient executes
    background tasks as part of the request cycle, so a scheduled bounce is
    observable as exactly one call alongside the 204.

    bounce_dashboard is patched AFTER the setup create (not before): create
    now also schedules its own deferred bounce through this same symbol
    (fix/create-modal-deferred-bounce), so patching earlier would double-count
    the create's bounce alongside the delete's."""
    import src.api.agents as agents_mod
    _create_tmp_agent(client)
    calls: list[str] = []
    monkeypatch.setattr(agents_mod, "bounce_dashboard", lambda: calls.append("bounce"))
    r = client.delete("/v1/agents/tmp", headers=_auth())
    assert r.status_code == 204
    assert calls == ["bounce"]


def test_delete_already_gone_does_not_bounce(client, monkeypatch):
    import src.api.agents as agents_mod
    calls: list[str] = []
    monkeypatch.setattr(agents_mod, "bounce_dashboard", lambda: calls.append("bounce"))
    r = client.delete("/v1/agents/never-existed", headers=_auth())
    assert r.status_code == 204
    assert calls == []


def test_delete_bounce_failure_does_not_break_the_204(client, monkeypatch):
    """A failing deferred bounce must never surface to the caller — the delete
    itself succeeded; the operator can re-bounce manually."""
    import src.api.agents as agents_mod

    def boom():
        raise RuntimeError("docker down")

    monkeypatch.setattr(agents_mod, "bounce_dashboard", boom)
    _create_tmp_agent(client)
    r = client.delete("/v1/agents/tmp", headers=_auth())
    assert r.status_code == 204


def _seed_agents_json(stack, entries):
    (stack / ".env").write_text(
        "HERMES_GATEWAY_KEY=k\n"
        f"AGENTS_JSON={json.dumps(entries, separators=(',', ':'))}\n"
    )


def test_list_prefers_live_profile_model_over_agents_json(client, fake_env):
    # AGENTS_JSON's model is a cache written only by orchestrator create/update;
    # `hermes model set` bypasses it. The API must serve the live config value.
    _seed_agents_json(fake_env["stack"], [{
        "id": "marketing-agent", "name": "Olivia",
        "gatewayUrl": "http://host.docker.internal:8643",
        "dashboardUrl": "http://host.docker.internal:9121",
        "color": "#7c3aed", "model": "gpt-5.5",
    }])
    profile = fake_env["profiles"] / "marketing-agent"
    profile.mkdir()
    (profile / "config.yaml").write_text(
        "model:\n  default: gpt-5.6-sol\n  provider: openai-codex\n"
    )
    r = client.get("/v1/agents", headers=_auth())
    assert r.status_code == 200
    agents = {a["id"]: a for a in r.json()["agents"]}
    assert agents["marketing-agent"]["model"] == "gpt-5.6-sol"


def test_default_agent_model_read_from_global_config(client, fake_env):
    # The default profile has no AGENTS_JSON model entry at all; its model
    # lives in ~/.hermes/config.yaml (fixture sets gpt-5.5).
    _seed_agents_json(fake_env["stack"], [{
        "id": "default", "name": "Ollie",
        "gatewayUrl": "http://host.docker.internal:8642",
        "dashboardUrl": "http://host.docker.internal:9119",
        "color": "#888888",
    }])
    r = client.get("/v1/agents", headers=_auth())
    assert r.status_code == 200
    agents = {a["id"]: a for a in r.json()["agents"]}
    assert agents["default"]["model"] == "gpt-5.5"


def test_model_falls_back_to_agents_json_when_no_profile_config(client, fake_env):
    _seed_agents_json(fake_env["stack"], [{
        "id": "ghost", "name": "Ghost",
        "gatewayUrl": "http://host.docker.internal:8650",
        "dashboardUrl": "http://host.docker.internal:9150",
        "color": "#123456", "model": "cached-model",
    }])
    r = client.get("/v1/agents", headers=_auth())
    assert r.status_code == 200
    agents = {a["id"]: a for a in r.json()["agents"]}
    assert agents["ghost"]["model"] == "cached-model"


def test_list_agents_includes_subtitle(client, fake_env):
    _seed_agents_json(fake_env["stack"], [{
        "id": "olivia", "name": "Olivia",
        "gatewayUrl": "http://host.docker.internal:8643",
        "dashboardUrl": "http://host.docker.internal:9121",
        "color": "#7c3aed", "model": "gpt-5.5",
        "subtitle": "AI Head of Marketing",
    }])
    resp = client.get("/v1/agents", headers=_auth())
    assert resp.status_code == 200
    agent = next(a for a in resp.json()["agents"] if a["id"] == "olivia")
    assert agent["subtitle"] == "AI Head of Marketing"


def test_list_agents_subtitle_null_when_unset(client, fake_env):
    _seed_agents_json(fake_env["stack"], [{
        "id": "olivia", "name": "Olivia",
        "gatewayUrl": "http://host.docker.internal:8643",
        "dashboardUrl": "http://host.docker.internal:9121",
        "color": "#7c3aed", "model": "gpt-5.5",
    }])
    resp = client.get("/v1/agents", headers=_auth())
    assert resp.status_code == 200
    agent = next(a for a in resp.json()["agents"] if a["id"] == "olivia")
    assert agent["subtitle"] is None


def test_update_subtitle_persists(client, fake_env):
    _seed_agents_json(fake_env["stack"], [{
        "id": "olivia", "name": "Olivia",
        "gatewayUrl": "http://host.docker.internal:8643",
        "dashboardUrl": "http://host.docker.internal:9121",
        "color": "#7c3aed", "model": "gpt-5.5",
    }])
    r = client.patch("/v1/agents/olivia", json={"subtitle": "Chief of Staff"}, headers=_auth())
    assert r.status_code == 200
    assert r.json()["subtitle"] == "Chief of Staff"
    from src.agents_json import read_agents
    entries = read_agents(fake_env["stack"] / ".env")
    entry = next(e for e in entries if e.id == "olivia")
    assert entry.subtitle == "Chief of Staff"


def test_update_subtitle_empty_string_clears(client, fake_env):
    _seed_agents_json(fake_env["stack"], [{
        "id": "olivia", "name": "Olivia",
        "gatewayUrl": "http://host.docker.internal:8643",
        "dashboardUrl": "http://host.docker.internal:9121",
        "color": "#7c3aed", "model": "gpt-5.5",
        "subtitle": "AI Head of Marketing",
    }])
    r = client.patch("/v1/agents/olivia", json={"subtitle": ""}, headers=_auth())
    assert r.status_code == 200
    assert r.json()["subtitle"] is None
    from src.agents_json import read_agents
    entries = read_agents(fake_env["stack"] / ".env")
    entry = next(e for e in entries if e.id == "olivia")
    assert entry.subtitle is None
    r2 = client.get("/v1/agents", headers=_auth())
    agent = next(a for a in r2.json()["agents"] if a["id"] == "olivia")
    assert agent["subtitle"] is None


def test_subtitle_too_long_rejected(client, fake_env):
    _seed_agents_json(fake_env["stack"], [{
        "id": "olivia", "name": "Olivia",
        "gatewayUrl": "http://host.docker.internal:8643",
        "dashboardUrl": "http://host.docker.internal:9121",
        "color": "#7c3aed", "model": "gpt-5.5",
    }])
    r = client.patch("/v1/agents/olivia", json={"subtitle": "x" * 65}, headers=_auth())
    assert r.status_code == 422


def test_update_other_field_does_not_wipe_subtitle(client, fake_env):
    # Task 1 review carry-over: update_agent's entry-rebuild must forward
    # entry.subtitle when the request doesn't touch it, or ANY update
    # silently wipes an existing subtitle.
    _seed_agents_json(fake_env["stack"], [{
        "id": "olivia", "name": "Olivia",
        "gatewayUrl": "http://host.docker.internal:8643",
        "dashboardUrl": "http://host.docker.internal:9121",
        "color": "#7c3aed", "model": "gpt-5.5",
        "subtitle": "AI Head of Marketing",
    }])
    r = client.patch("/v1/agents/olivia", json={"color": "#111111"}, headers=_auth())
    assert r.status_code == 200
    assert r.json()["subtitle"] == "AI Head of Marketing"
    from src.agents_json import read_agents
    entries = read_agents(fake_env["stack"] / ".env")
    entry = next(e for e in entries if e.id == "olivia")
    assert entry.subtitle == "AI Head of Marketing"


def test_patch_sets_and_clears_avatar_url(client, fake_env):
    _seed_agents_json(fake_env["stack"], [{
        "id": "olivia", "name": "Olivia",
        "gatewayUrl": "http://host.docker.internal:8643",
        "dashboardUrl": "http://host.docker.internal:9121",
        "color": "#7c3aed", "model": "gpt-5.5",
    }])
    # set
    r = client.patch("/v1/agents/olivia", json={"avatar_url": "https://x/shared/olivia.jpg?t=1"},
                      headers=_auth())
    assert r.status_code == 200
    assert r.json()["avatar_url"] == "https://x/shared/olivia.jpg?t=1"
    # list surfaces it
    r2 = client.get("/v1/agents", headers=_auth())
    agent = next(a for a in r2.json()["agents"] if a["id"] == "olivia")
    assert agent["avatar_url"] == "https://x/shared/olivia.jpg?t=1"
    # clear with ""
    r = client.patch("/v1/agents/olivia", json={"avatar_url": ""}, headers=_auth())
    assert r.status_code == 200
    assert r.json()["avatar_url"] is None
    from src.agents_json import read_agents
    entries = read_agents(fake_env["stack"] / ".env")
    entry = next(e for e in entries if e.id == "olivia")
    assert entry.avatar_url is None


def test_patch_sets_and_clears_voice(client, fake_env):
    _seed_agents_json(fake_env["stack"], [{
        "id": "olivia", "name": "Olivia",
        "gatewayUrl": "http://host.docker.internal:8643",
        "dashboardUrl": "http://host.docker.internal:9121",
        "color": "#7c3aed", "model": "gpt-5.5",
    }])
    # set
    r = client.patch("/v1/agents/olivia", json={"voice": "en-GB-RyanNeural"},
                      headers=_auth())
    assert r.status_code == 200
    assert r.json()["voice"] == "en-GB-RyanNeural"
    # list surfaces it
    r2 = client.get("/v1/agents", headers=_auth())
    agent = next(a for a in r2.json()["agents"] if a["id"] == "olivia")
    assert agent["voice"] == "en-GB-RyanNeural"
    # clear with ""
    r = client.patch("/v1/agents/olivia", json={"voice": ""}, headers=_auth())
    assert r.status_code == 200
    assert r.json()["voice"] is None
    from src.agents_json import read_agents
    entries = read_agents(fake_env["stack"] / ".env")
    entry = next(e for e in entries if e.id == "olivia")
    assert entry.voice is None


def test_list_agents_includes_voice(client, fake_env):
    _seed_agents_json(fake_env["stack"], [{
        "id": "olivia", "name": "Olivia",
        "gatewayUrl": "http://host.docker.internal:8643",
        "dashboardUrl": "http://host.docker.internal:9121",
        "color": "#7c3aed", "model": "gpt-5.5",
        "voice": "en-GB-RyanNeural",
    }])
    resp = client.get("/v1/agents", headers=_auth())
    assert resp.status_code == 200
    agent = next(a for a in resp.json()["agents"] if a["id"] == "olivia")
    assert agent["voice"] == "en-GB-RyanNeural"


def test_list_agents_voice_null_when_unset(client, fake_env):
    _seed_agents_json(fake_env["stack"], [{
        "id": "olivia", "name": "Olivia",
        "gatewayUrl": "http://host.docker.internal:8643",
        "dashboardUrl": "http://host.docker.internal:9121",
        "color": "#7c3aed", "model": "gpt-5.5",
    }])
    resp = client.get("/v1/agents", headers=_auth())
    assert resp.status_code == 200
    agent = next(a for a in resp.json()["agents"] if a["id"] == "olivia")
    assert agent["voice"] is None


def test_list_agents_includes_scope(client, fake_env):
    _seed_agents_json(fake_env["stack"], [{
        "id": "olivia", "name": "Olivia",
        "gatewayUrl": "http://host.docker.internal:8643",
        "dashboardUrl": "http://host.docker.internal:9121",
        "color": "#7c3aed", "model": "gpt-5.5",
        "scope": "user",
    }])
    resp = client.get("/v1/agents", headers=_auth())
    assert resp.status_code == 200
    agent = next(a for a in resp.json()["agents"] if a["id"] == "olivia")
    assert agent["scope"] == "user"


def test_get_agent_includes_scope(client, fake_env):
    _seed_agents_json(fake_env["stack"], [{
        "id": "olivia", "name": "Olivia",
        "gatewayUrl": "http://host.docker.internal:8643",
        "dashboardUrl": "http://host.docker.internal:9121",
        "color": "#7c3aed", "model": "gpt-5.5",
        "scope": "company",
    }])
    resp = client.get("/v1/agents/olivia", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["scope"] == "company"


def test_create_schedules_deferred_dashboard_bounce(client, monkeypatch):
    """The bounce must run as a BackgroundTask AFTER the SSE body is sent, not
    inside the generator: it recreates the container housing the nginx that
    proxies this very response, so an inline call cancels the request task and
    the browser never sees the eight progress events (GetBilled, 2026-07-28)."""
    import src.api.agents as agents_mod
    calls: list[str] = []
    monkeypatch.setattr(agents_mod, "bounce_dashboard", lambda: calls.append("bounce"))
    _create_tmp_agent(client)
    assert calls == ["bounce"]


def test_create_failure_does_not_bounce(client, monkeypatch):
    """A create that failed has already rolled back; restarting the dashboard
    container would be pure disruption."""
    import src.api.agents as agents_mod
    _create_tmp_agent(client)          # first one succeeds and bounces
    calls: list[str] = []
    monkeypatch.setattr(agents_mod, "bounce_dashboard", lambda: calls.append("bounce"))
    body = {"name": "tmp", "provider": "anthropic", "model": "m",
            "apiKey": "k", "enabledSkills": []}
    r = client.post("/v1/agents", json=body, headers=_auth())   # duplicate name
    events = []
    for raw in r.iter_lines():
        if isinstance(raw, bytes):
            raw = raw.decode()
        if raw.startswith("data: "):
            events.append(json.loads(raw[6:]))
    assert any(ev.get("event") == "error" for ev in events)
    assert calls == []


def test_create_bounce_failure_does_not_break_the_stream(client, monkeypatch):
    """A failing deferred bounce must never surface to the caller — the create
    itself succeeded."""
    import src.api.agents as agents_mod

    def boom():
        raise RuntimeError("docker down")

    monkeypatch.setattr(agents_mod, "bounce_dashboard", boom)
    body = {"name": "tmp", "provider": "anthropic", "model": "m",
            "apiKey": "k", "enabledSkills": []}
    r = client.post("/v1/agents", json=body, headers=_auth())
    assert r.status_code == 202
    events = []
    for raw in r.iter_lines():
        if isinstance(raw, bytes):
            raw = raw.decode()
        if raw.startswith("data: "):
            events.append(json.loads(raw[6:]))
    assert any(ev.get("event") == "done" for ev in events)


def test_create_response_carries_anti_buffering_headers(client):
    """Cloudflare buffers text/event-stream when it transforms it; no-transform
    is the documented opt-out, and X-Accel-Buffering pairs with the
    proxy_buffering off already in the generated agents.conf."""
    body = {"name": "tmp", "provider": "anthropic", "model": "m",
            "apiKey": "k", "enabledSkills": []}
    r = client.post("/v1/agents", json=body, headers=_auth())
    list(r.iter_lines())
    assert "no-transform" in r.headers["cache-control"]
    assert r.headers["x-accel-buffering"] == "no"


def test_create_writes_an_audit_row(client, fake_env):
    """The audit call sits at the tail of stream(); when the bounce cancelled
    the request task it never ran, so UI-created agents went unrecorded."""
    _create_tmp_agent(client)
    log = fake_env["home"] / ".local" / "state" / "ollie-orchestrator" / "audit.log"
    rows = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
    creates = [r for r in rows if r["op"] == "create" and r["agent_id"] == "tmp"]
    assert len(creates) == 1
    assert creates[0]["result"] == "ok"


@pytest.mark.asyncio
async def test_create_disconnect_after_done_still_bounces_and_audits(fake_env, monkeypatch):
    """Reproduces the real race, not through TestClient (which always drains
    the whole SSE body and never disconnects mid-stream) but by driving the
    ASGI app directly with a hand-rolled receive/send pair.

    This server runs Starlette 1.0.1, which advertises ASGI spec_version 2.3
    (< 2.4) and so takes StreamingResponse's task-group path: an anyio task
    group runs stream_response(send) and listen_for_disconnect(receive)
    concurrently, and whichever finishes first cancels the other via
    task_group.cancel_scope.cancel(). A real browser closes the create modal
    the moment it sees "done" and may drop the connection while that very
    chunk is still being written — so this test's fake send() sets a flag
    the instant it receives the "done" chunk and then hangs forever (as a
    broken/closed socket write effectively would), while the fake receive()
    only resolves to http.disconnect once that flag is set. That guarantees
    the cancellation strikes stream_response while it is suspended inside
    `await send(...)` for the "done" chunk — never handing control back to
    our stream() generator, which stays suspended at its `yield` and is
    never resumed through ordinary iteration.

    Two things must still hold under that cancellation:
    1. bounce_state["needed"] must already be True by the time
       self.background() runs — Starlette's StreamingResponse.__call__ runs
       it unconditionally after the (cancelled) task group exits, disconnect
       or not, so if bounce_state weren't set until the code after the loop,
       it would silently return with needed=False.
    2. The audit row must exist despite stream()'s generator never being
       resumed through normal iteration. Nothing in this call chain closes
       it explicitly: CPython's async-generator finalizer (registered on the
       running loop by BaseEventLoop.run_forever, confirmed with
       sys.get_asyncgen_hooks()) is what eventually calls aclose() on an
       abandoned, unreferenced async generator, delivering GeneratorExit and
       running stream()'s `finally`. That finalizer only *schedules* a task
       rather than running synchronously inline, so this test forces the
       collection deterministically (gc.collect() plus draining the loop)
       instead of sleeping and hoping — this was verified empirically: the
       audit row is absent immediately after app() returns and present once
       the loop is drained, confirming the finally genuinely runs via
       GeneratorExit rather than via ordinary fall-through completion.
    """
    import gc
    import src.api.agents as agents_mod
    from src.api.main import create_app

    calls: list[str] = []
    monkeypatch.setattr(agents_mod, "bounce_dashboard", lambda: calls.append("bounce"))

    app = create_app()
    body = json.dumps({"name": "tmp", "provider": "anthropic", "model": "m",
                        "apiKey": "k", "enabledSkills": []}).encode()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},  # < 2.4: task-group path
        "http_version": "1.1",
        "method": "POST",
        "path": "/v1/agents",
        "raw_path": b"/v1/agents",
        "query_string": b"",
        "headers": [
            (b"authorization", b"Bearer topsecret"),
            (b"content-type", b"application/json"),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }

    body_sent = False
    seen_done = anyio.Event()

    async def receive():
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        # listen_for_disconnect's next call: only resolve once the "done"
        # chunk has actually been handed to send() — the same race as the
        # browser closing the modal on "done" and the socket going away.
        await seen_done.wait()
        return {"type": "http.disconnect"}

    sent_messages = []

    async def send(message):
        sent_messages.append(message)
        if message.get("type") == "http.response.body" and b"event: done" in message.get("body", b""):
            seen_done.set()
            # A broken/closed socket write never returns; hang here so the
            # cancellation strikes exactly inside this await, matching the
            # real race rather than a send that completes cleanly first.
            await anyio.sleep_forever()

    # Bounded so a regression that makes this hang (e.g. the cancellation
    # never landing) fails the test instead of the suite.
    with anyio.fail_after(10):
        await app(scope, receive, send)

    assert any(m.get("type") == "http.response.start" and m.get("status") == 202
               for m in sent_messages)

    # (1) bounce_state was set before the abandoned yield, so the background
    # task still bounces even though stream() was never resumed normally.
    assert calls == ["bounce"]

    # (2) force the async-generator finalizer that the running loop already
    # has registered (see docstring) to actually run, rather than relying on
    # incidental GC timing.
    gc.collect()
    for _ in range(50):
        await anyio.sleep(0)

    log = fake_env["home"] / ".local" / "state" / "ollie-orchestrator" / "audit.log"
    assert log.exists(), "audit row missing: stream()'s finally never ran under cancellation"
    rows = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
    creates = [r for r in rows if r["op"] == "create" and r["agent_id"] == "tmp"]
    assert len(creates) == 1
    assert creates[0]["result"] == "ok"
