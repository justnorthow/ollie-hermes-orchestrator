# Dispatch tools via Cortex — design

**Date:** 2026-07-30
**Status:** approved in conversation, not yet planned
**Supersedes:** `plugins/dispatch/` in this repo, which cannot load — see
`2026-07-30-dispatch-plugin-loading-spike.md`
**Implements in:** `ollie-hermes-cortex`, not this repo

## Why this exists

The switchable-dispatch work shipped an orchestrator API (`/v1/dispatch/*`) and a
Hermes-side plugin (`plugins/dispatch/`). The API is correct and in use-ready
shape. The plugin cannot load: Hermes has no generic tool-plugin category, and
`get_tool_schemas()` / `handle_tool_call()` / `initialize(session_id)` is the
**`MemoryProvider`** contract. The spike doc has the evidence.

The supported alternative — an MCP server — cannot carry the Hermes conversation
session id, so it cannot carry provenance, and provenance is the entire basis of
the design.

**Cortex already satisfies every requirement.** It is a memory provider, so it is
loaded and its tools are called. It receives `initialize(session_id)` and stashes
it (`provider.py:44-45`). It already implements `get_tool_schemas()` and
`handle_tool_call()`. And it is our own repo, so nothing depends on upstream.

So: host the dispatch tools inside Cortex.

## The trade we are making

This is deliberate impurity. Dispatch tools in a memory plugin is not where they
belong architecturally, and we are choosing it because it is the only option that
ships with the security model intact.

| Cost | Assessment |
|---|---|
| Two concerns in one plugin | Real. Contained by keeping dispatch in its own module and touching the provider only at three seams. |
| Dispatch releases couple to Cortex releases | Real. Acceptable — both are ours and deploy together. |
| Boxes without Cortex get no dispatch | Real. Both current boxes run it. |
| Upstream could tighten what a memory plugin may expose | Low, unquantifiable. Mitigated by keeping the tool surface small; exit is the upstream `tools` category. |
| ~~Wiped by `hermes update`~~ | **Not a cost.** Vendored plugins are untracked and survive the stash/pull/restore. Verified on both boxes. |

The exit remains open: if a generic tool category lands upstream, the dispatch
module moves out of Cortex nearly unchanged.

## Where the code goes

New module `plugins/memory/cortex/dispatch.py` in `ollie-hermes-cortex`, holding
everything dispatch-specific. `provider.py` changes at exactly three seams:

1. `get_tool_schemas()` — append the dispatch schemas when dispatch is enabled
2. `handle_tool_call()` — delegate the two dispatch tool names
3. `system_prompt_block()` — append the dispatch rules when enabled

Nothing else in Cortex changes. `initialize()` already stores the session id.

Port from `ollie-hermes-orchestrator/plugins/dispatch/`: the HTTP client, the
refusal vocabulary (`reasons.py`), the transport-failure classification, and the
off-mode gating. That code was reviewed and is sound; it changes address, not
substance.

**Two shape changes on the way over.** Cortex's tool schemas use `"parameters"`,
not `"input_schema"`. And `handle_tool_call(name, args) -> str` returns a JSON
string, not a dict. Both are Cortex conventions and the ported code must match
them, not the other way round.

## Isolation — the constraint that outranks the feature

**Cortex's memory tools must keep working no matter what dispatch does.** Memory
is load-bearing on both boxes; dispatch is new and optional. A dispatch bug that
breaks `memory_search` is a far worse outcome than dispatch not working.

Concretely:

- Dispatch state is constructed lazily and its construction cannot raise into
  `__init__`. A failure to configure dispatch leaves a working memory plugin.
- `get_tool_schemas()` returns the memory schemas even if the dispatch block
  raises. The dispatch schemas are appended inside a guard.
- `handle_tool_call()` routes memory names first. A dispatch name that fails
  returns a structured refusal; it never propagates.
- No dispatch import at module scope in `provider.py` that could fail the plugin
  load.

This is testable and should be tested explicitly: with dispatch misconfigured in
every way we can think of, the four memory tools still work.

## Behaviour

Unchanged from the reviewed design, restated here so this spec stands alone.

**Modes.** `DISPATCH_MODE` in the profile environment: `off` (default) or
`direct`. Anything else — including the reserved `local` and `linear` — behaves
as `off`. In `off` mode Cortex exposes **zero** dispatch tools and **zero**
dispatch prompt text; the agent's context is identical to a box where this was
never built.

**Tools.** `list_teammates` (no arguments) and `ask_teammate` (`to_agent`,
`question`). No `assign_task` — there is no durable queue in this slice, and a
tool that implies one would license the model to claim work was assigned.

**Provenance.** Every call sends `from_agent` (from `DISPATCH_AGENT_ID`) and
`session_id` (from `initialize()`). The orchestrator resolves the human via
`get_session_owner(agent_id, session_id)` and refuses when it cannot. Nothing
about the human is asserted by the model.

**Refusals.** Every failure is a structured refusal, never an exception and never
an empty string. An exception or empty tool result reaching a language model is
precisely when it invents a plausible answer and presents another agent's
supposed opinion as fact. The plugin's own vocabulary — auth failure, connection
failure, timeout, and a generic error — stays disjoint from the server's
`REASON_*` set so a reader can tell which side refused.

**System prompt block.** Two rules, only when enabled: never fabricate a
teammate's answer, and never describe work as assigned or handed off — `direct`
is consult-only.

## Deployment prerequisites

One configuration change, and one cheap verification, stand before this
can be planned and enabled.

### 0. RETRACTED — the session ids do match

An earlier revision of this spec recorded a BLOCKER here: that the session id
Cortex receives is a different namespace from what `agent_sessions` stores, so
every consult would refuse `forbidden`. **That was wrong and is retracted.**

The run path in `gateway/platforms/api_server.py`:

```python
session_id = body.get("session_id") or stored_session_id   # :6150
...
run_id = f"run_{uuid.uuid4().hex}"                          # :6163
session_id = session_id or run_id                           # :6164
```

With no client-supplied id, **Hermes uses the run id as the session id**. With
one, it adopts it verbatim. Either way the value equals what the orchestrator
stores — and `src/api/runs.py:336` pre-claims that run id as the session row on
purpose, with a comment saying so: "Hermes v0.18 emits no session_id in a first
run's SSE frames, so the frontend's done-event fallback reuses THIS run id as
session_id on the thread's next message."

So the `run_*` values in `agent_sessions` are not a foreign namespace. They are
the session id, by construction.

**How the error was made, since it is worth not repeating:** the `api_<ts>_<hex>`
format at `api_server.py:3291` was read and generalised to the run path. That
line belongs to the **fork** endpoint. One code path was read and a conclusion
drawn about a different one.

**What is still genuinely unverified** is the last hop: that `agent.session_id`
— the value `initialize(session_id)` hands the provider — is this same string
rather than something re-derived inside the agent. The evidence points that way
(the run path threads `session_id` into the agent construction and the run
status), but it has not been observed end to end. Settle it with one live run:
create a run through the orchestrator, read the session id Hermes reports back,
and compare it to the run id and to the `agent_sessions` row. Cheap, and it
converts the last assumption into an observation.

### 1. A consultable specialist must run a `fast` model

Consult eligibility comes from the model catalog's `speed_class`. `fast` peers
can be consulted inline; `heavy` peers are listed but not consult-eligible, so a
slow expensive model can never block another agent's turn.

**Karl M currently runs `gpt-5.6-sol`, which is `heavy`.** He is the only
specialist on the box. So with today's config, dispatch would deploy and Billie
could consult nobody.

Move Karl to `gpt-5.6-terra` (classified `fast`, the balanced tier) for consults
to work. Sol remains appropriate for work driven directly by a human; only inline
consults need the fast class.

Without this change slice 1 delivers nothing observable, which is worth knowing
before building rather than after.

### 2. Nothing — scope no longer gates consults

An earlier draft of this spec listed a scope/tier prerequisite here. It no
longer applies: the roster filter was removed on 2026-07-30.

`scope` and `manager_visible` govern how a **human** reaches an agent, so a
user is not left working out which specialist to ask. They do not limit which
peers an agent may consult — any agent may consult any agent. `list_teammates`
returns the whole bench at every tier.

The consequence to be deliberate about: enabling dispatch widens effective
information access without changing any tier or scope, because anyone who can
reach an agent can reach, through it, what every other agent knows. It is
bounded by consults being read-only, and every crossing is stamped on the audit
row as `beyond_human_reach` so it stays auditable after the fact.

Billie is `scope: "user"` on the GetBilled box (changed 2026-07-30) so any
signed-in user reaches her. Karl M remains `scope: "company"`. That still
governs whether a human can open Karl **directly** — it no longer affects
whether Billie can consult him on their behalf.

## Testing

The ported logic keeps its tests. Add:

- **Memory survives dispatch failure** — the isolation property above, tested
  against several distinct misconfigurations.
- **Off-mode inertness** — no dispatch tool names and no dispatch prompt text in
  `off`, `local`, `linear`, and a typo. Verify by guard deletion, not by
  assertion alone: an acceptance test that passes on arrival must be shown to
  fail when the guard is removed.
- **Schema shape** — dispatch schemas use `"parameters"` and sit alongside the
  memory schemas without colliding on names.
- **Session id reaches the payload** — `initialize()` then a consult, asserting
  the outgoing `session_id`. This is the property the whole approach exists for.

## Out of scope

`assign_task`, the durable queue, heartbeats, the dashboard queue view, and the
Linear adapter. Those are the later slices and none are unblocked by this work.

Removing `plugins/dispatch/` from the orchestrator repo is also out of scope for
now — it stays as reference for the port, with the runbook's stop notice
preventing anyone deploying it. Delete it once this lands.
