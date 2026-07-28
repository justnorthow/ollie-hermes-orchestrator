# Create-agent modal: defer the dashboard bounce past the response

Date: 2026-07-28
Base: `1bde3e4` (main)
Status: approved for implementation

## Problem

Creating an agent through the dashboard UI shows a progress modal with eight steps.
The modal renders none of them: all eight sit unchecked indefinitely, while the agent is
built correctly underneath. Users refresh, find the agent present, and retry — the
retry is what produced a duplicate-name attempt on GetBilled.

Observed on the eSource/GetBilled box on 2026-07-27 (`mail-agent`) and again on
2026-07-28 (`paige`).

### Root cause

`POST /v1/agents` returns a `StreamingResponse` of SSE progress events. In
`src/api/agents.py` the `audit(...)` call sits after the `async for` loop that drains
the generator. In `src/lifecycle.py` `create_agent` yields `{"event": "done"}` and then,
on the consumer's next pull, calls `bounce_dashboard()` — `docker compose up -d
dashboard`, which recreates the `ollie-dashboard` container. That container houses the
nginx proxying this very response.

So: yield `done` into a socket, destroy the thing holding the socket, the request task
is cancelled, and the code after the loop never runs.

**The evidence is the missing audit row.** `paige` was created cleanly at
2026-07-28 22:22:55–22:22:58 (journal confirms profile, config, both systemd units) and
is fully present in `AGENTS_JSON`, both proxy maps, and her session-token drop-in — yet
the audit log has no `create` entry for her, and no exception was logged. Execution
stops exactly at the bounce, which is what a clean task cancellation looks like.

The deferred-bounce fix of 2026-07-17 moved `bounce_dashboard()` after the `done` yield
for precisely this reason. That was necessary but not sufficient: yielding is not
flushing through nginx and Cloudflare to the browser. The response still dies in flight.

### Two consequences

1. The modal never completes, so users retry a create that already succeeded.
2. The audit log silently undercounts UI-created agents. Any create whose modal hung is
   absent from it — which matters, because it is the first place we look when
   diagnosing whether a create happened.

## Non-goals

- Changing the eight-step SSE contract or the frontend. The step list stays as it is.
- Removing the bounce. It is required: the dashboard container's nginx regenerates its
  per-agent `/dashboard-proxy/<id>/` location blocks at container start, so a new agent
  has no route until it runs.
- Guaranteeing Cloudflare streams the events incrementally. See "Confidence" below.

## Design

### Part 1 — move the bounce past the response

`create_agent` (`src/lifecycle.py`) stops calling `bounce_dashboard()`. Its generator
ends after yielding `done`. `bounce_dashboard` has no other caller in that module, so
its import goes with it; `src/api/agents.py` already imports the same symbol for the
delete path and keeps doing so.

**Critical distinction.** The string `"bounce_dashboard"` also appears as an SSE *step
name* — `yield _ev("bounce_dashboard")` and the matching `completed_steps.append(...)`
at `src/lifecycle.py:171-172`. That step is part of the eight-step contract the frontend
modal renders and MUST stay exactly as it is. Only the actual `bounce_dashboard()` call
at `src/lifecycle.py:198`, its surrounding `try/except`, and the now-dead import are
removed. Deleting the step event would break the very modal this change exists to fix.

`create()` (`src/api/agents.py`) attaches the bounce as a Starlette background task,
mirroring `_bounce_after_delete` in the same module:

```python
return StreamingResponse(
    stream(), media_type="text/event-stream", status_code=202,
    headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    background=BackgroundTask(_bounce_after_create, cfg, actor_ip, body.name, bounce_state),
)
```

`bounce_state` is a one-key dict the stream sets to `{"needed": True}` only when the
result event is `done`, mirroring delete's `bounce_needed` — a failed create has already
rolled back and must not restart the container.

This is `starlette.background.BackgroundTask` (the object), not FastAPI's
`BackgroundTasks` dependency, because the response is constructed by hand.

Starlette runs `background` only after the response body is fully sent, so the container
can no longer be destroyed underneath its own response.

`_bounce_after_create` must never raise — a raising background task poisons the request
in tests and logs — and audits its own failure, exactly as `_bounce_after_delete` does.

**Consequence 2 is fixed for free.** With the task no longer cancelled, the `audit(...)`
call at the tail of `stream()` executes again.

### Part 2 — anti-buffering response headers

`Cache-Control: no-cache, no-transform` and `X-Accel-Buffering: no`.

`no-transform` is the documented way to tell Cloudflare not to modify the response;
compression is the usual reason a CDN buffers `text/event-stream`. `X-Accel-Buffering:
no` is belt-and-braces beside the `proxy_buffering off` already present in the
generated `agents.conf` block.

### Accepted consequence

The new agent's nginx route appears a second or two after the modal closes, because the
bounce now trails the response. `delete` already behaves this way.

## Confidence

Part 1 is high confidence: known mechanism, established in-codebase pattern, and it
directly explains the missing audit row.

Part 2 is the highest-probability code-side lever, not a guarantee. If Cloudflare
buffers for a reason response headers do not control, the remaining fix is a zone or
tunnel setting in the Cloudflare dashboard rather than code — the same shape as the
HTTP/2-origin finding on the Towns box on 2026-07-27. One real create on GetBilled
settles it.

## Testing

- `create_agent` no longer calls `bounce_dashboard` inline.
- A successful create schedules the bounce, and it runs.
- A failed create does not bounce.
- A failing bounce neither breaks the stream nor goes unaudited.
- The response carries both anti-buffering headers.
- The audit row is written for a successful create.

## Verification

Create one agent on the GetBilled box and observe the modal. Then confirm the audit log
contains the `create` row, and that `check-box-config.sh` still reports done-done.
