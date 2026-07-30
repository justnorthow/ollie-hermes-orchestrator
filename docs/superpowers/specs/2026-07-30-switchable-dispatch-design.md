# Switchable agent-to-agent dispatch — Design

Date: 2026-07-30
Base: `df70ee9` (main)
Status: draft — awaiting review

**Spans three repos.** Read the file citations carefully: `ollie-hermes-orchestrator`
(the mediator and its API), `ollie-hermes-install` (migrations, install scripts,
systemd units), and a new tool plugin. Migrations do **not** live in the
orchestrator repo — they are `ollie-hermes-install/supabase/ollie-core/*.sql`.

## Problem

An Ollie box runs several agents — on the GetBilled instance, a chief of staff
over an email specialist and (soon) a design/web specialist. **No agent can reach
another.** Each is a separate host-native Hermes profile with its own gateway and
dashboard systemd services, registered in `AGENTS_JSON`. Nothing in the platform
lets one agent ask another a question or hand it work.

Today the only available behaviour is: an agent names the specialist and the exact
ask, and the human switches chats. That is honest but it costs a human round trip
for every hop, and it means a chief-of-staff agent cannot actually hold a loop that
spans two specialists.

We want dispatch, but not uniformly. JNOW's own instance already routes work through
Linear (the Open Engine queue, heartbeat-driven, with a receipt vocabulary). A
client box does not want that weight. So dispatch has to be **switchable per
instance**, from "off" through to "the full Linear queue".

## Non-goals

- **Agent autonomy beyond its human's authority.** Dispatch must not become a path
  by which one agent grants another permission a human never gave. This is the
  central safety property; §3 is about nothing else.
- **Replacing Open Engine.** The Linear mode wraps it, it does not supersede it.
- **Cross-box dispatch.** All routing in this design is within one instance. A
  fleet-wide dispatch bus is a different (and much larger) problem.
- **Changing how humans reach agents.** `authz.can_reach` and the dashboard's
  agent list are untouched.
- **Search-then-update semantics in the Linear adapter** beyond what Open Engine
  already does.
- **Upstream `hermes-agent` changes.** See the constraint below.

## The binding constraint

`hermes-agent` is **upstream third-party code we do not own**, and `hermes update`
wipes files patched inside it. `ollie-hermes-install/scripts/07-patch-cron-brain.sh`
exists solely to re-apply two edits to `cron/scheduler.py` and `run_agent.py` after
every update, and its own header says "Re-apply after every `hermes update`".

> **Dispatch must ship as a tool plugin plus external services, requiring zero
> upstream patches.** Anything needing a `hermes-agent` edit inherits that
> re-patch-forever tax, and a missed re-application would silently disable
> dispatch on a customer box.

Fortunately the supported path exists.
`ollie-hermes-install/scripts/04-install-cortex-plugin.sh:15` records it:

> "User-installed plugins via `hermes plugins install` are for tool plugins, not
> memory providers."

Memory providers are the special case that must be vendored into the bundled tree
(and are wiped by `hermes update`). **Tool plugins have a supported, upgrade-safe
install path** — which is exactly what dispatch is.

## What already exists

Five of the six things dispatch needs are already built. This is why the design is
small.

| Need | Already there |
|---|---|
| Agent-to-agent transport | Each gateway exposes an OpenAI-compatible `/v1/chat/completions` on `127.0.0.1:{gateway_port}`; the orchestrator already calls it — `src/persona_polish.py:42` |
| Roster discovery | `AgentEntry` with `gateway_port` and `scope`/`manager_visible` — `src/agents_json.py:15,18,25`, parsed from `AGENTS_JSON` |
| **Provenance resolution** | **`get_session_owner(agent, session_id) -> user_id`** — `src/api/sessions.py:91`, querying `agent_sessions` by `(agent_id, hermes_session_id, instance_id)`. Already load-bearing for run ownership at `src/api/runs.py:83` and `src/api/sessions.py:318,332` |
| Session ownership records | `record_session()` — `src/api/sessions.py:112`, and the table at `ollie-hermes-install/supabase/ollie-core/0001_agent_sessions.sql` |
| Human authz precedent | `can_reach(tier, scope, manager_visible)` — `src/api/authz.py:17` |
| Append-only audit trail | `public.governance_events` — `ollie-hermes-install/supabase/ollie-core/0005_governance_events.sql`, service-role-write-only with RLS select policies |
| Plugin contract | `get_tool_schemas()`, `handle_tool_call()`, `system_prompt_block()`, `get_config_schema()` — the Cortex provider demonstrates all four |
| **Plugin receives its session id** | `initialize(self, session_id: str, **kwargs)` — Cortex's `provider.py:44` stores it and forwards it on every sync |
| Speed/cost metadata | `speed_class`, `price_in`/`price_out`, `long_context_threshold` on every catalog entry — `src/catalog.py`, added by the catalog-freshness work |

The missing piece is the mediator and the protocol.

## Design

### 1. The plugin talks to the orchestrator, never to a peer gateway

```
Billie's Hermes agent
  └─ hermes-dispatch tool plugin      (list_teammates / ask_teammate / assign_task)
       │  POST 127.0.0.1:9123/v1/dispatch/*   [ORCHESTRATOR_KEY]
       ▼
  Orchestrator  src/dispatch/
       ├─ resolve provenance   get_session_owner(agent, session_id) -> user_id
       ├─ enforce authority    min(origin human, target agent) — never elevated
       ├─ enforce caps         hop / depth / cycle / fan-out / token budget
       ├─ write queue          public.dispatch_tasks        (local, linear)
       ├─ write audit          public.governance_events     (all modes)
       └─ backend driver ──►  off | direct | local | linear
                                   │
                               direct: POST peer gateway /v1/chat/completions
```

**Three reasons it must route this way rather than gateway-to-gateway**, even
though the direct call is one line:

1. **The shared key is not a boundary.** One `HERMES_GATEWAY_KEY` covers every
   profile on the box (`~/hermes-stack/.env`, and the same value in each
   profile's env). Any agent that can reach a peer's port can already impersonate
   any other agent to it. Peer-to-peer dispatch would be unauthenticated in every
   sense that matters.
2. **Provenance must be resolved, not asserted.** An agent claiming "John asked
   for this" proves nothing — it is model output. The orchestrator looks up
   `agent_sessions` and *derives* the human. A calling agent cannot lie about who
   it acts for, because it never states it.
3. **Audit and authz already live there.** The orchestrator holds the service-role
   key, `authz.py`, and the `governance_events` write path. A dispatch path that
   bypassed it would be unauditable, which is the governance hole this feature
   would otherwise open.

Cost: one extra hop of loopback latency. Worth it.

### 2. The protocol

**One task shape, one state machine, shared by `local` and `linear`.** The backend
is a driver; the protocol is the standard. This is what lets a skill written
against `local` run unchanged on `linear`, which is the whole point of a Fleet.

```
states:  pending_approval → todo → working → needs_input → review → done
                 ↓                                ↓          ↓
             cancelled                         failed     cancelled
```

Receipts reuse the **existing Open Engine vocabulary verbatim** — `AGENT CLAIMED`,
`DONE`, `BLOCKED`, `UNBLOCKED`, `HUMAN HOLD`, `HUMAN ANSWERED`, `RESUMED`,
`FAILED`, `FOLLOW-UP`, `STATUS`. No second vocabulary is invented. The `linear`
adapter maps states to Linear statuses and receipts to comments exactly as
`jb-claude` does today; the `local` adapter writes the same tokens to its own rows.

**Task row** (`public.dispatch_tasks`, new migration in the install repo):

| Column | Purpose |
|---|---|
| `id`, `created_at`, `updated_at` | identity |
| `instance_id` | which box (matches the `governance_events` / `agent_sessions` convention) |
| `from_agent`, `to_agent` | routing |
| `origin_user_id` | **resolved server-side**, never supplied by the caller |
| `chain` (jsonb) | `["John", "billie", "karl-m"]` — for cycle detection and audit |
| `hop` | integer, for the depth cap |
| `task_class` | `read` \| `build` \| `spend` |
| `state` | the state machine above |
| `title`, `body` | the ask |
| `result` | the answer, when done |
| `run_id` | traceability into Hermes runs |

RLS on, select policies only, service-role-write-only — the same shape
`agent_sessions` and `governance_events` already use.

### 3. Authority, provenance, and the gate

This is the section that matters most.

**Provenance is resolved, not asserted.** The plugin sends its own `agent_id` and
the `session_id` Hermes gave it at `initialize()`. The orchestrator calls the
*existing* `get_session_owner(agent, session_id)` to obtain `user_id`, then
`roles.resolve_tier(instance_id, user_id)` for the tier. A request whose session
does not resolve to a user is **rejected**, not defaulted — fail closed, matching
`authz.py:50`'s existing "unknown agent → forbidden, don't leak existence".

**Authority never escalates.** Effective authority on a task is **the origin
human's tier, and nothing may exceed it** — no chain of agents can manufacture
authority that did not enter at the top. Concretely: Billie cannot grant Karl a
send approval John never gave.

Note what does *not* exist today: there is no machine-readable per-agent authority
ceiling. `scope` and `manager_visible` (`src/agents_json.py:25`) govern which
*humans* may reach an agent, not what an agent may do. So v1's ceiling is the human
tier alone. A per-agent ceiling — "Karl may never be assigned a `spend` task" — is a
natural extension and its home is an optional field on `AgentEntry`; it is
deliberately out of scope here, and the spec should not be read as implying it
exists.

**The declared `task_class` is a hint from the caller, not a trusted control.**
The calling agent supplies it, and a model can under-declare — marking a paid
enrichment as `read` to skip the assign-time gate. Two things bound that:

1. **The output gate does not consult `task_class` at all.** Sends, publishes, and
   deploys are blocked for any agent-authored task regardless of how it was
   labelled, so under-declaring cannot ship anything.
2. **Spend is bounded at the executing agent, not at the label.** The gap the
   label alone leaves open is burning paid credits (Apollo enrichment, Firecrawl)
   on work nobody approved. The control for that is the *executing* agent's own
   tool-level approval, plus a per-instance list of which classes each agent may
   accept — the orchestrator rejects an `assign` whose class is not on the target's
   accept-list, and the accept-list is operator config, not agent-supplied.

Treating the class as authoritative on its own would be a real hole. It is worth
saying plainly because the tiered-gate table reads as if the class were a control,
and it is not — it is a routing hint whose worst case is bounded by the two rules
above.

**The gate is tiered by task class**, which lines up with the authority model
already chosen for Billie:

| Class | Examples | Assign gate |
|---|---|---|
| `read` | research, review, analysis, answer a question | auto-approve |
| `build` | drafts, configs, repo commits, WP pages staged as drafts | auto-approve — matches "wider autonomy on internals" |
| `spend` | paid credits (Apollo enrichment, Firecrawl), or anything producing outbound artifacts | **human approves before work starts** |

**The output gate is unconditional and independent of class.** Sends, publishes,
deploys, deletes, credential and billing changes always require a human — and the
load-bearing rule is that **an agent-authored task can never satisfy that gate.**
Enforced in the orchestrator, not in SOUL.md prose. Prose is not a control; it is a
description of a control that exists elsewhere.

**Every state transition writes a `governance_events` row** with `app='dispatch'`,
`event_type='dispatch_<transition>'`, the resolved `user_email`/`user_role`, and
`run_id`. No parallel audit log.

### 4. The four modes

| Mode | Surface | New dependencies |
|---|---|---|
| **`off`** *(default)* | Plugin `is_available()` → False. No tool schemas, no system-prompt block. | none |
| **`direct`** | `list_teammates`, `ask_teammate`. `assign_task` returns `not_enabled`. | none |
| **`local`** | + `assign_task`, `check_assignments`, queue, heartbeat, dashboard view | migration + timer |
| **`linear`** | + Open Engine adapter | Linear credentials on the box |

**`off` must be genuinely inert** — not "registered but refusing". Towns and jnow
prod are live boxes; their agents' tool lists and system prompts must be
byte-identical after this ships. That is an explicit acceptance test, not an
assumption.

### 5. Heartbeat — avoiding the patch tax

**Do not use upstream `cron/scheduler.py`.** `07-patch-cron-brain.sh` is the
evidence: cron sessions run with a deliberately reduced capability surface
(`skip_memory=True` hard-coded upstream), fixing it required patching upstream
files, and `hermes update` wipes them. Whether *tool* plugins surface in a cron
session is unverified, and betting the heartbeat on it buys a second subscription
to that tax.

**Instead: an orchestrator-owned heartbeat.** A `systemd --user` timer per
instance — the pattern `ollie-hermes-install/scripts/02/03/05/09` already
establish — fires the orchestrator, which POSTs a "check your queue" turn to the
gateway of each agent *that actually has queued work*.

Zero upstream dependency, survives `hermes update`, and strictly better than a
blind poll: agents with empty queues burn no inference at all. Default cadence 15
minutes, per-instance configurable. `direct` mode needs no heartbeat.

### 6. Consult latency, caps, and failure

**Consult blocks the calling agent's turn, with a cheap-peer rule.** A consult is
synchronous: Billie asks, waits, and continues in the same reply. That is what
makes it feel like a colleague rather than a ticket. To stop a heavyweight peer
freezing a turn, consults are permitted only to peers whose model is
`speed_class: fast`.

**This now reads a field that already exists** — `src/catalog.py` carries
`speed_class` on every entry, populated by the catalog-freshness work. As of
2026-07-30: `claude-sonnet-5`, `claude-haiku-4-5`, `gpt-5.6-terra`,
`gpt-5.6-luna`, `llama-3.3-70b` are `fast`; `claude-opus-5` and `gpt-5.6-sol` are
`heavy`.

A consult aimed at a `heavy` peer does **not** fail — it converts to an `assign`,
and the tool result says so, so the agent reports "that's a bigger ask, I've
queued it" instead of implying it got an answer.

**Cost is bounded by more than speed class.** `long_context_threshold` exists
because a cheap peer becomes expensive above a token threshold — the GPT-5.6
family applies 2.0× input / 1.5× output above ~272k input tokens. A consult that
would carry a large working context to a peer is therefore checked against the
target's threshold and converted to an `assign` if it would cross it.

**Caps** (defaults, all per-instance configurable): hop cap 3 · reject if the
target already appears in `chain` (cycle) · fan-out cap 5 open tasks per agent ·
per-chain token budget as a hard ceiling · consult timeout 30s.

**Failure semantics — the part that protects the humans:**

- Every failure returns a **structured** tool result:
  `{"ok": false, "reason": "timeout" | "peer_unavailable" | "cap_exceeded" | "not_enabled" | "forbidden" | "converted_to_assign"}`. Never an empty string, never a raw exception.
- `system_prompt_block()` carries the hard rule: **never fabricate a teammate's
  answer, and never describe work as assigned unless the tool returned a task id.**
  This is the same failure mode Billie's SOUL.md guards against in prose — now
  backed by the tool contract, which is the version that actually holds.
- Timeouts do not orphan work. `direct` is stateless. `local`/`linear` tasks stay
  `working` and a stale sweep surfaces them on the next heartbeat.

### 7. Config surface

Through the plugin's `get_config_schema()` → dashboard, per instance:
`DISPATCH_MODE`, hop cap, fan-out cap, consult timeout, per-chain token budget,
heartbeat cadence, and which `speed_class` values are consult-eligible. Mode
changes require a gateway restart, the same as identity changes today.

## Risks and open questions

**1. Is the plugin's `session_id` the same string as `agent_sessions.hermes_session_id`?**
This is the one unknown the whole provenance design rests on. Both halves exist —
Hermes passes `session_id` into `initialize()` (Cortex `provider.py:44`), and
`get_session_owner()` looks up exactly that column — but nothing in this codebase
proves they are the same value, because Cortex never joins them.

**Spike this before slice 1 is finalised.** It is a one-session experiment: run an
agent, capture the `session_id` a plugin sees, and compare it against the
`hermes_session_id` the orchestrator recorded for that run. If they differ, the
fallback is orchestrator-side correlation on `run_id`, which is a design change
rather than an implementation detail — hence spiking first.

**2. Do `hermes plugins install` plugins survive `hermes update`?** Memory plugins
demonstrably do not. The install script's comment implies tool plugins do, but
"implied by a comment" is not "verified". One command on a box settles it; if they
do not, the playbook gains a re-install step next to scripts 04 and 07.

**3. The `local` queue's storage lands mid-pivot.** `dispatch_tasks` belongs in
`ollie-core` `public`, alongside `agent_sessions` and `governance_events` — it is
platform infrastructure, not app data, so the per-app-schema decision of
2026-07-29 does not apply to it. That keeps it clear of the in-flight pivot, but
the migration ordering should be confirmed against whatever `ollie-core` migration
number is current when slice 3 starts.

**4. Consult blocks a turn for up to 30s with no UI feedback.** Accepted for v1.
Streaming progress ("asking Karl…") requires verifying that a tool plugin can emit
mid-turn status through the gateway's stream — unverified, and a follow-up.

**5. Consult spend is invisible in the moment.** `governance_events` gives
retrospective visibility; a per-instance spend line in the dashboard is a
follow-up, not part of this design.

**6. `off` must be provably inert.** The acceptance test is a byte-comparison of an
agent's tool list and system prompt before and after installing the plugin with
`DISPATCH_MODE=off`. If that is not clean, existing customer boxes are at risk and
the feature does not ship.

**7. The per-agent class accept-list is new operator config with no home yet.**
§3 leans on it to bound the under-declared-`task_class` case, but nothing in
`AGENTS_JSON` or the orchestrator config carries it today. Slice 1 must add it —
either as an optional field on `AgentEntry` or as a `DISPATCH_ACCEPTS_<agent>` env
entry alongside the existing per-instance dispatch config. Until it exists, `spend`
assignment should be refused outright rather than auto-approved, which is the
fail-closed reading and costs nothing in `direct` mode (where `assign` is disabled
anyway).

## Slices

1. **Protocol + core.** `src/dispatch/` interface, provenance resolution reusing
   `get_session_owner`, authority + caps, `governance_events` writes, backend
   registry with `off` + `direct`. Ships `off`/`direct`.
2. **The plugin.** `hermes-dispatch` via `hermes plugins install`; tool schemas,
   system-prompt block, config schema. *Billie can consult Karl at the end of this
   slice.*
3. **Local backend.** `dispatch_tasks` migration (install repo), queue driver,
   orchestrator heartbeat timer, stale sweep.
4. **Dashboard.** Queue view, approve/reject on `pending_approval`, mode selector.
5. **Linear backend.** Open Engine adapter behind the settled interface.

**Scope decision, JB 2026-07-30: build slices 1–2 only, `direct`-only to start.**

That is a smaller change than it first appears, and three dependencies drop out
entirely:

- **No migration.** `dispatch_tasks` exists to hold a *queue*; `direct` has none. The
  migration moves to slice 3, so slices 1–2 touch no database schema and no install
  repo at all — they are orchestrator changes plus one new plugin.
- **No per-agent class accept-list.** `assign` is disabled in `direct`, so the
  under-declared-`task_class` hole (§3) is unreachable: a consult is a `read` by
  construction. Risk 7 is deferred with slice 3.
- **No heartbeat, no stale sweep, no dashboard queue view.**

What remains binding: provenance resolution, authority, the caps, the cheap-peer
rule, the structured failure contract, `governance_events` writes, and `off` being
provably inert.

**On the provenance spike (Risk 1):** it does not block implementation, because the
resolution path is fail-closed. If the `session_id` a plugin receives does not match
`agent_sessions.hermes_session_id`, `get_session_owner` returns `None` and dispatch
**refuses every request** rather than defaulting to a permissive identity. A mismatch
is therefore loud and safe on first use rather than silent and dangerous — the first
real consult on a box *is* the spike. Build it; do not wait for it.

## Relationship to the catalog work

The cheap-peer rule consumes `speed_class` and `long_context_threshold` from
`src/catalog.py`. Both were added by the model-catalog-freshness spec
(`2026-07-29-model-catalog-freshness-design.md`) precisely so this design would
consume an existing field rather than introduce one, and the weekly check keeps
them from going stale — a dispatch rule reading a catalog nobody maintains would
degrade silently.

`src/catalog_declined.py` matters here too: a declined model never becomes a
consult target, because it is never in `MODELS`.
