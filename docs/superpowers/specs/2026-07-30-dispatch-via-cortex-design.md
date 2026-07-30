# Dispatch tools via Cortex — design

**Date:** 2026-07-30 (rewritten same day — see *Revision history*)
**Status:** approved in conversation, ready to plan
**Supersedes:** `plugins/dispatch/` in this repo, which cannot load — see
`2026-07-30-dispatch-plugin-loading-spike.md`
**Implements in:** `ollie-hermes-cortex`, not this repo

## Why this exists

The orchestrator half of dispatch shipped and works: `/v1/dispatch/*`, the
authority model, provenance resolution, the audit trail, the in-flight bound.
The Hermes half — `plugins/dispatch/` — cannot load.

Hermes has no generic tool-plugin category. `get_tool_schemas()` /
`handle_tool_call()` / `system_prompt_block()` / `get_config_schema()` /
`initialize(session_id)` is the **`MemoryProvider`** contract; all ten
production implementers on a live box live under `plugins/memory/`.
`DispatchProvider` implements that shape without being registered as one, so
nothing will ever call it.

The supported alternative is MCP (`hermes mcp add`), but an MCP tool call
cannot carry the Hermes conversation session id — `session.call_tool(name,
arguments=args)` passes arguments only, and MCP headers are static per
registration. Without a session id there is no provenance, and provenance is
the basis of the whole design.

**Cortex satisfies every requirement.** It is a memory provider, so it loads and
its tools are called. It receives `initialize(session_id)` and already stashes
it. It already implements the tool hooks. It is our own repo, so nothing waits
on upstream.

## What has been verified, and when

Everything below was checked against live boxes on 2026-07-30. It is recorded
because two earlier conclusions in this area were wrong, both from reading one
code path and generalising to another.

| Claim | Status |
|---|---|
| Hermes hands the provider `agent.session_id` | ✅ `agent/agent_init.py:1653` |
| Cortex stashes it | ✅ `plugins/memory/cortex/provider.py:44-45` |
| That id equals `agent_sessions.hermes_session_id` | ✅ **measured** — a live run returned `session_id == run_id == run_44371e29…`, and the matching `agent_sessions` row resolved the owner. `api_server.py:6164` is `session_id = session_id or run_id`. |
| Karl M is consult-eligible | ✅ `gpt-5.6-terra` (`fast`) in both `AGENTS_JSON` and his profile config |
| Vendored plugins survive `hermes update` | ✅ untracked; the update stashes with `--include-untracked` and restores. No `git clean` in the update path. |
| Scope/tier does not gate consults | ✅ removed 2026-07-30 — any agent may consult any agent |

**The one thing still unverified** is the last hop: that the string
`initialize(session_id)` receives is that same session id rather than something
re-derived inside the agent. Everything points that way and the run-path
evidence is strong, but it has not been observed from inside a provider. It is
a Task-1 test below rather than an assumption.

## The trade

Dispatch tools in a memory plugin is not where they belong. We are choosing it
because it is the only option that ships with the security model intact.

| Cost | Assessment |
|---|---|
| Two concerns in one plugin | Real. Contained by keeping dispatch in its own module and touching the provider at three seams. |
| Dispatch releases couple to Cortex releases | Acceptable — both are ours and deploy together. |
| Boxes without Cortex get no dispatch | Both current boxes run it. |
| Upstream could tighten what a memory plugin may expose | Low. Mitigated by a small tool surface; the exit is an upstream `tools` category, and the module moves out nearly unchanged if that lands. |

## Where the code goes

New module `plugins/memory/cortex/dispatch.py` in `ollie-hermes-cortex`.
`provider.py` changes at exactly three seams:

1. `get_tool_schemas()` — append dispatch schemas when enabled
2. `handle_tool_call()` — delegate the two dispatch tool names
3. `system_prompt_block()` — append the dispatch rules when enabled

`initialize()` already stores the session id; nothing else in Cortex changes.

Port from `ollie-hermes-orchestrator/plugins/dispatch/`: the refusal
vocabulary, the transport-failure classification, and the mode gating. That
code was reviewed and is sound — but it must be **adapted to Cortex's
conventions, not transplanted**:

| Cortex convention | What the ported code does today |
|---|---|
| Tool schemas use `"parameters"` | uses `"input_schema"` |
| `handle_tool_call(name, args) -> str` returns a JSON **string** | returns a dict |
| `CortexHttpClient` uses stdlib **`urllib.request`** | uses `httpx` |
| Its client hardcodes `timeout=10` | dispatch needs **75s** (server worst case 60s) |
| Its client raises `RuntimeError` on HTTP error | dispatch must convert to a structured refusal |

The stdlib choice is deliberate for a vendored plugin — do not introduce an
httpx dependency to save porting effort. The 10s timeout means dispatch needs
its own client or a timeout parameter; do not raise Cortex's memory timeout to
75s as a shortcut.

## Isolation — the constraint that outranks the feature

**Cortex's memory tools must keep working no matter what dispatch does.**
Memory is load-bearing on both boxes; dispatch is new and optional. A dispatch
bug that breaks `memory_search` is a far worse outcome than dispatch not
working at all.

- Dispatch state is constructed lazily; its construction cannot raise into
  `__init__`.
- `get_tool_schemas()` returns the memory schemas even if the dispatch block
  raises — dispatch schemas are appended inside a guard.
- `handle_tool_call()` routes memory names first. A dispatch name that fails
  returns a structured refusal and never propagates.
- No dispatch import at module scope in `provider.py` that could fail the load.

This is the requirement most likely to be traded away by an implementer
optimising for clean structure. It gets its own task and its own tests.

## Behaviour

**Modes.** `DISPATCH_MODE` in the profile environment: `off` (default) or
`direct`. Anything else — including the reserved `local` and `linear` —
behaves as `off`. In `off` mode Cortex exposes **zero** dispatch tools and
**zero** dispatch prompt text: an agent's context is identical to a box where
this was never built.

**Tools.** `list_teammates` (no arguments) and `ask_teammate` (`to_agent`,
`question`). No `assign_task` — there is no durable queue in this slice, and a
tool implying one would license the model to claim work was assigned.

**Provenance.** Every call sends `from_agent` (from `DISPATCH_AGENT_ID`) and
`session_id` (from `initialize()`). The orchestrator resolves the human via
`get_session_owner(agent_id, session_id)` and refuses when it cannot. Nothing
about the human is asserted by the model.

**Reach.** Any agent may consult any agent. `scope` / `manager_visible` govern
how *humans* reach agents — they keep a picker uncluttered so a user need not
work out which specialist to ask — and deliberately do not narrow an agent's
roster. Where the origin human could not have opened the peer directly, the
orchestrator stamps `beyond_human_reach` on the audit row. Recorded, not
blocked.

**Refusals.** Every failure is a structured refusal — never an exception, never
an empty string. An exception or empty tool result reaching a language model is
exactly when it invents a plausible answer and presents another agent's
supposed opinion as fact. The plugin's own vocabulary (auth failure, connection
failure, timeout, generic error) stays disjoint from the server's `REASON_*`
set so a reader can tell which side refused.

**System prompt block.** Two rules, only when enabled: never fabricate a
teammate's answer, and never describe work as assigned or handed off —
`direct` is consult-only.

## Testing

The ported logic keeps its tests. Add:

- **The session id reaches the payload.** Call `initialize()` with a known id,
  then a consult, and assert the outgoing `session_id` is that exact string.
  This is the last unverified hop and the property the whole approach rests on.
- **Memory survives dispatch failure.** The isolation property above, against
  several distinct misconfigurations — missing env, unreachable orchestrator,
  malformed config, dispatch module raising on import.
- **Off-mode inertness.** No dispatch tool names and no dispatch prompt text in
  `off`, `local`, `linear`, and a typo. **Verify by guard deletion**, not by
  assertion alone: a test that passes on arrival must be shown to fail when the
  guard is removed.
- **Schema shape.** Dispatch schemas use `"parameters"`, `handle_tool_call`
  returns a JSON string, and the names do not collide with the memory tools.

**A note on verification method, learned the hard way today:** a check without a
negative control can report false safety. `/api/status` on Hermes needs no auth
at all, so "the old token still returns 200" looked like a failed rotation and
was not. Where a test asserts something is gated, include the ungated control.

## Deployment

No prerequisites remain. Karl is on Terra, the provenance chain is measured, and
scope no longer gates consults. Enabling is:

1. `DISPATCH_MODE=direct` on **both** the profile env and the orchestrator env —
   two processes, two environments. See `docs/runbooks/agent-dispatch.md`.
2. `DISPATCH_AGENT_ID`, `ORCHESTRATOR_URL`, `ORCHESTRATOR_KEY` in the profile env.
3. Restart the profile's gateway.

## Out of scope

`assign_task`, the durable queue, heartbeats, the dashboard queue view, and the
Linear adapter — later slices, none unblocked by this work.

Deleting `plugins/dispatch/` from the orchestrator repo happens when this lands;
until then it stays as porting reference behind the runbook's stop notice.

## Revision history

Rewritten 2026-07-30 rather than patched. The first version accumulated a
retracted BLOCKER and two rewritten prerequisites, and had become an archaeology
site rather than a statement of what is true. Superseded content:

- A prerequisite claiming the session ids were different namespaces. **Wrong** —
  `api_server.py:6164` derives the session id from the run id, now measured on a
  live box. The error came from reading the *fork* endpoint (`:3291`) and
  generalising to the run path.
- A prerequisite requiring Karl to move to a `fast` model. **Done.**
- A prerequisite about scope/tier admitting the right people. **Obsolete** — the
  roster filter was removed the same day.
- A cost line claiming vendored plugins are wiped by `hermes update`. **Wrong** —
  they are untracked and survive the stash/pull/restore.
