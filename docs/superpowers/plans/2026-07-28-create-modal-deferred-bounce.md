# Create-Agent Modal Deferred Bounce Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the create-agent dashboard bounce from cancelling its own SSE response, so the modal renders its eight steps and completes, and the audit row stops going missing.

**Architecture:** Move `bounce_dashboard()` out of the `create_agent` generator entirely and attach it to the `StreamingResponse` as a `starlette.background.BackgroundTask`, which Starlette runs only after the body is fully sent. Mirrors `_bounce_after_delete`, already in the same module for the same reason. Add anti-buffering response headers for the Cloudflare hop.

**Tech Stack:** Python 3.11+, FastAPI/Starlette, pytest (`asyncio_mode = auto`, `pythonpath = .`).

## Global Constraints

- The eight-step SSE contract is unchanged. `yield _ev("bounce_dashboard")` and its `completed_steps.append("bounce_dashboard")` at `src/lifecycle.py:171-172` MUST survive verbatim — that is the step name the frontend modal renders, not the call being removed.
- Only these are removed from `src/lifecycle.py`: the `bounce_dashboard()` call and its `try/except` at `:191-200`, and the now-dead import at `:9`.
- `_bounce_after_create` must never raise — a raising background task poisons the request in tests and logs — and must audit its own failure, exactly as `_bounce_after_delete` does.
- A failed create must NOT bounce: it has already rolled back.
- Branch: `fix/create-modal-deferred-bounce`, base `1bde3e4`, spec commit `bb1935f`. Test baseline before any change: **573 passed, 1 skipped**.

## File Structure

| File | Responsibility |
|---|---|
| `src/lifecycle.py` (modify) | Stops bouncing. Generator ends after `yield done`. |
| `src/api/agents.py` (modify) | Owns the deferred bounce and the response headers. |
| `tests/test_api_agents.py` (modify) | Coverage for scheduling, non-scheduling, failure containment, headers, audit row. |

---

### Task 1: Defer the bounce and harden the stream

**Files:**
- Modify: `src/lifecycle.py:9`, `src/lifecycle.py:165-172`, `src/lifecycle.py:191-200`
- Modify: `src/api/agents.py:9-22` (imports), and the `create()` function at `:93-131`
- Test: `tests/test_api_agents.py` (append)

**Interfaces:**
- Consumes: existing `bounce_dashboard()` from `src.docker_ops` (already imported in `src/api/agents.py:16`); existing `audit(log_path, *, op, agent_id, actor_ip, result, duration_ms, error=None)` from `src.audit`; existing module logger `_logger` at `src/api/agents.py:24`.
- Produces: `_bounce_after_create(cfg, actor_ip: str, agent_id: str, state: dict) -> None`, scheduled via `BackgroundTask`. No other task depends on it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api_agents.py`. The existing `client` fixture, `_auth()` and `_create_tmp_agent(client)` helpers at the top of that file are already what you need — do not redefine them.

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_api_agents.py -v -k "create_schedules or create_failure_does_not_bounce or anti_buffering or audit_row"`

Expected: `test_create_schedules_deferred_dashboard_bounce` FAILS with `assert [] == ['bounce']` (the real bounce is called from `src.lifecycle`, not the patched `agents_mod` symbol); `test_create_response_carries_anti_buffering_headers` FAILS with `KeyError: 'x-accel-buffering'`. The audit-row test may already pass under TestClient, which does not reproduce the real cancellation — that is expected and it stays as a regression guard.

- [ ] **Step 3: Remove the bounce from the generator**

In `src/lifecycle.py`, delete the import on line 9:

```python
from src.docker_ops import bounce_dashboard
```

Then replace the comment block at lines 165-170 (keeping the two lines below it EXACTLY as they are):

```python
            # 9. UX-level "bounce_dashboard" step — we emit the event now (so
            # the progress UI shows it ticking) but defer the ACTUAL bounce
            # until after we yield "done". The dashboard container houses
            # the nginx proxying this SSE stream; bouncing it before "done"
            # tears down the connection and the browser never learns the
            # create succeeded, leading to a confused user-retry.
```

with:

```python
            # 9. UX-level "bounce_dashboard" step. The event is emitted here so
            # the progress UI ticks, but the ACTUAL bounce is not this module's
            # job at all — it runs as a BackgroundTask attached to the response
            # in api/agents.py, after the SSE body has been fully sent. The
            # dashboard container houses the nginx proxying that stream, so
            # bouncing it anywhere inside this generator, even after "done",
            # cancels the request task mid-flight: the browser rendered none of
            # the eight steps and the audit row at the tail of stream() never
            # ran (diagnosed on GetBilled via the missing 'paige' row,
            # 2026-07-28).
```

Then delete lines 191-200 entirely — the `# 11. actually bounce dashboard ...` comment block together with its `try/except`:

```python
            # 11. actually bounce dashboard now that the SSE response has
            # delivered "done". Browser closes the modal as it processes
            # "done"; the nginx restart on the tail of this response is
            # fine because the client doesn't need any more events.
            # Wrapped in try/except: a bounce failure doesn't undo a
            # successful create. Operator can re-bounce manually if needed.
            try:
                bounce_dashboard()
            except Exception:
                _logger.warning("bounce_dashboard failed after successful create", exc_info=True)
```

The `yield {"event": "done", ...}` immediately above it stays, and `except Exception as exc:` immediately below it stays.

- [ ] **Step 4: Add the deferred bounce to the API layer**

In `src/api/agents.py`, add this import after line 10 (`from fastapi.responses import StreamingResponse`):

```python
from starlette.background import BackgroundTask
```

Note this is the `BackgroundTask` OBJECT, not FastAPI's `BackgroundTasks` dependency already imported on line 9 — the latter cannot be used on a hand-constructed response. Both now coexist; leave line 9 alone.

Add this function immediately before the `@router.post("", status_code=status.HTTP_202_ACCEPTED)` decorator:

```python
def _bounce_after_create(cfg, actor_ip: str, agent_id: str, state: dict) -> None:
    """Runs as a BackgroundTask after the create's SSE body has been fully
    sent. bounce_dashboard() recreates the ollie-dashboard container, which
    houses the nginx proxying this very response — calling it inside the
    generator cancelled the request task mid-flight, so the browser rendered
    none of the eight progress events and the audit row at the tail of
    stream() never ran (diagnosed on the GetBilled box via the missing
    'paige' create row, 2026-07-28). Mirrors _bounce_after_delete below.
    Must never raise: a raising background task poisons the request in tests
    and logs."""
    if not state.get("needed"):
        # A failed create already rolled back; a bounce would be pure disruption.
        return
    try:
        bounce_dashboard()
    except Exception as e:
        _logger.warning("create: deferred dashboard bounce failed", exc_info=True)
        audit(cfg.audit_log_path, op="create", agent_id=agent_id, actor_ip=actor_ip,
              result="error", duration_ms=0, error=f"deferred bounce failed: {e}")
```

- [ ] **Step 5: Wire the response**

Still in `src/api/agents.py`, in `create()`, replace this block:

```python
    async def stream():
        result_event = None
        async for ev in create_agent(req):
            if ev.get("event") in ("done", "error"):
                result_event = ev
                yield sse_event(event=ev["event"], data=ev)
            else:
                yield sse_event(event="progress", data=ev)
        result = "ok" if (result_event or {}).get("event") == "done" else "error"
        duration = (result_event or {}).get("duration_ms", 0)
        audit(cfg.audit_log_path, op="create", agent_id=body.name,
              actor_ip=actor_ip, result=result, duration_ms=duration,
              error=(result_event or {}).get("error"))

    return StreamingResponse(stream(), media_type="text/event-stream", status_code=202)
```

with:

```python
    # Set by stream() once the outcome is known, read by the background task
    # after the body has been sent. Mirrors delete's `bounce_needed`.
    bounce_state: dict = {"needed": False}

    async def stream():
        result_event = None
        async for ev in create_agent(req):
            if ev.get("event") in ("done", "error"):
                result_event = ev
                yield sse_event(event=ev["event"], data=ev)
            else:
                yield sse_event(event="progress", data=ev)
        result = "ok" if (result_event or {}).get("event") == "done" else "error"
        bounce_state["needed"] = result == "ok"
        duration = (result_event or {}).get("duration_ms", 0)
        audit(cfg.audit_log_path, op="create", agent_id=body.name,
              actor_ip=actor_ip, result=result, duration_ms=duration,
              error=(result_event or {}).get("error"))

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        status_code=202,
        # Cloudflare buffers text/event-stream when it transforms it, and
        # compression is the usual trigger; no-transform is the documented
        # opt-out. X-Accel-Buffering pairs with the proxy_buffering off already
        # in the generated agents.conf block.
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
        background=BackgroundTask(_bounce_after_create, cfg, actor_ip, body.name, bounce_state),
    )
```

- [ ] **Step 6: Run the new tests**

Run: `python -m pytest tests/test_api_agents.py -v`
Expected: PASS, all tests in the file including the five new ones.

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS, 578 passed, 1 skipped (baseline 573 + 5 new).

- [ ] **Step 8: Commit**

```bash
git add src/lifecycle.py src/api/agents.py tests/test_api_agents.py
git commit -m "fix(agents): defer the create bounce past the SSE response

create_agent called bounce_dashboard() after yielding done, which recreates
the ollie-dashboard container — the container housing the nginx that proxies
that very SSE response. The request task was cancelled mid-flight, so the
browser rendered none of the eight progress steps and users refreshed and
retried a create that had already succeeded.

The evidence is the missing audit row: 'paige' was created cleanly on
GetBilled 2026-07-28 22:22:55, is present in AGENTS_JSON, both proxy maps and
her session-token drop-in, no exception logged — yet no create row exists,
because audit() sits after the drained loop and never ran. The 2026-07-17 fix
moved the call after the done yield, which is necessary but not sufficient:
yielding is not flushing to the browser.

Attach the bounce as a starlette BackgroundTask instead, which runs only once
the body is fully sent, mirroring _bounce_after_delete in the same module. A
failed create does not bounce; a failing bounce is swallowed and audited. Add
Cache-Control: no-transform and X-Accel-Buffering: no so the Cloudflare hop
does not buffer the stream it is now able to deliver.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Deploy to GetBilled and verify against a real create

**Files:** none — deployment and verification only.

**Interfaces:**
- Consumes: Task 1, merged to `main`.
- Produces: a verdict on whether the eight steps visibly tick, which decides whether a Cloudflare follow-up is needed.

- [ ] **Step 1: Merge and push**

```bash
python -m pytest -q
git checkout main && git merge --ff-only fix/create-modal-deferred-bounce && git push origin main
```

Expected: 578 passed, 1 skipped; fast-forward; push succeeds.

- [ ] **Step 2: Deploy to GetBilled only**

This change is UI-behaviour-affecting, so it goes to one box first rather than all four. No new dependencies, so the orchestrator repo update plus a restart is sufficient — do NOT pull the install repo.

```bash
ssh ollie-getbilled 'git -C ~/ollie-hermes-orchestrator fetch -q origin main && git -C ~/ollie-hermes-orchestrator reset -q --hard origin/main && git -C ~/ollie-hermes-orchestrator rev-parse --short HEAD && systemctl --user restart ollie-orchestrator && sleep 4 && systemctl --user is-active ollie-orchestrator'
```

Expected: the new HEAD short SHA, then `active`.

- [ ] **Step 3: Ask the human partner to create one agent and report what the modal did**

This is the whole point of the change and cannot be verified from the server side. Ask for:
- whether the eight steps ticked one by one, appeared all at once at the end, or still hung
- whether the modal closed by itself

Do not proceed to Step 4 until they answer.

- [ ] **Step 4: Confirm the server-side half regardless of what the modal did**

```bash
ssh ollie-getbilled 'tail -3 /home/ollie/.local/state/ollie-orchestrator/audit.log; OPERATOR_EMAIL=jb@jnow.io bash ~/ollie-hermes-install/scripts/check-box-config.sh | tail -2'
```

Expected: a `create` row for the new agent with `result: ok` — its presence is the proof the request task was no longer cancelled — and `OK: box config is done-done`.

- [ ] **Step 5: Route the outcome**

- Steps ticked one by one → done. Roll out to jnow prod, sandbox and Towns with the Step 2 command.
- Steps appeared all at once at the end, or the modal closed instantly → the server fix worked and Cloudflare is buffering. Report that plainly; the remaining lever is a zone/tunnel setting in the human partner's Cloudflare dashboard, not code. Do not guess at settings — present the evidence and let them decide.
- Still hung with no audit row → the fix did not work. STOP and report; do not attempt a second fix without re-diagnosing.

---

## Self-Review

**Spec coverage.** Move the bounce out of the generator → Task 1 Steps 3-5. Never raise, audit its own failure → Step 4. Failed create must not bounce → `bounce_state`, Step 5, tested. Anti-buffering headers → Step 5, tested. Audit row restored → covered by the same change, tested. Eight-step contract preserved → Global Constraints plus Step 3's explicit "keep these two lines". Verification on GetBilled → Task 2.

**Placeholder scan.** No TBDs; every code step carries the actual code; the one step that cannot be automated (Step 3 of Task 2, watching the modal) names exactly what to ask.

**Type consistency.** `_bounce_after_create(cfg, actor_ip: str, agent_id: str, state: dict)` is defined in Step 4 and called with exactly that argument order in Step 5's `BackgroundTask(...)`. `audit(...)` keywords match `src/audit.py:21-31`. `bounce_state` is the same name in both steps.

**Known limitation.** TestClient runs background tasks synchronously within the request cycle and does not reproduce the real cancellation, so the new tests prove the bounce is *scheduled rather than inline* — they cannot prove the browser now receives the events. Only Task 2 Step 3 can. This is stated rather than papered over.
