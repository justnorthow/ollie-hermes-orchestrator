# Agent-to-agent dispatch (`direct` mode)

Design, orchestrator side:
`docs/superpowers/specs/2026-07-30-switchable-dispatch-design.md`.
Design, agent side (the tools, hosted in Cortex):
`docs/superpowers/specs/2026-07-30-dispatch-via-cortex-design.md`.

Both halves are built, merged and tested. Enabling is configuration only — see
below. The one thing that will bite you is that `DISPATCH_MODE` must be set in
**two** environments.

## What it does

One agent asks another agent a question and gets the answer back in the same
turn — `ask_teammate` in, an answer out, nothing queued. It **cannot** give
anyone work. Task/queue dispatch (`assign_task`, `check_assignments`, the
`dispatch_tasks` table, the heartbeat sweep) is a later slice and does not
exist yet; if you find yourself wanting an agent to hand off a task rather
than ask a question, that isn't built.

## Enabling it: two processes, two environments

**Read this whole section before changing anything.** The plugin and the
orchestrator are separate processes with separate environments, and each reads
its own copy of `DISPATCH_MODE`. Setting it on only one side is the most likely
way to end up with a box that looks enabled and refuses every call.

- **Per profile** (the Hermes agent process) `DISPATCH_MODE` controls the
  *plugin surface*: whether the agent is offered the tools and the prompt block
  at all.
- **Instance-wide** (the orchestrator process) `DISPATCH_MODE` controls whether
  any consult is actually *permitted*. `current_mode()` in
  `src/api/dispatch.py` reads the orchestrator's own environment, so this one
  value gates **every profile on the box at once**. There is no per-profile
  server-side switch.

Set both, or the tools appear and every call returns `not_enabled` — whose
table entry below reads "dispatch is off on this instance", which the operator
has just proven to themselves is false.

### 1. Orchestrator side (once per box)

In `~/.config/ollie-orchestrator/.env` (the file `scripts/install.sh` creates,
alongside `ORCHESTRATOR_KEY`):

```bash
DISPATCH_MODE=direct
```

Confirm `HERMES_GATEWAY_KEY` in that same file is **not blank** — `install.sh`
writes it empty for you to paste into later, and dispatch cannot reach any peer
gateway without it. A blank key refuses every consult with `misconfigured`.

Then restart the orchestrator:

```bash
systemctl --user restart ollie-orchestrator
```

### 2. Per profile, for each agent that should be able to ask

In that profile's environment:

```bash
DISPATCH_MODE=direct
DISPATCH_AGENT_ID=<this-agent's-id>   # must match its id in AGENTS_JSON
ORCHESTRATOR_URL=http://127.0.0.1:9123
ORCHESTRATOR_KEY=<the same value as the orchestrator's ORCHESTRATOR_KEY>
```

`ORCHESTRATOR_KEY` is **required**: the plugin authenticates to the
orchestrator with it, and without it every call 401s and comes back as
`orchestrator_auth_failed`. `ORCHESTRATOR_URL` defaults to
`http://127.0.0.1:9123` and only needs setting if the orchestrator is not on
loopback's default port.

Then restart that profile's gateway:

```bash
systemctl --user restart hermes-gateway              # the default profile
systemctl --user restart hermes-gateway-<profile>    # any other profile
```

**The agent-side tools live in Cortex, not in a plugin of their own.** There is
nothing to `hermes plugins install`. If a box already runs the Cortex memory
provider, it already has the code — the env vars above plus a gateway restart
are the whole install.

Confirm Cortex is actually the active provider before you expect any of this to
work, since that is the real prerequisite:

```bash
grep -A3 '^memory:' ~/.hermes/config.yaml          # the default profile
ls ~/.hermes/hermes-agent/plugins/memory/cortex/   # the code is present
```

Why: Hermes has **no generic tool-plugin category**. `get_tool_schemas()` /
`handle_tool_call()` / `initialize(session_id)` is the **`MemoryProvider`**
contract, and every production implementer on a live box lives under
`plugins/memory/`. `plugins/dispatch/` in *this* repo was built against a
contract that does not exist for tools and **cannot load**; it survives only as
porting reference and will be deleted. MCP (`hermes mcp add`) is the supported
way to add tools, but an MCP tool call cannot carry the Hermes conversation
session id, so it cannot carry provenance — the basis of this whole design.

Cortex is the only surface that both loads *and* receives the session id. The
tools live at `ollie-hermes-cortex/plugins/memory/cortex/dispatch.py`; the
provider delegates at three seams and holds no dispatch logic of its own.

Evidence and the options considered:
`docs/superpowers/specs/2026-07-30-dispatch-plugin-loading-spike.md`.
Design: `docs/superpowers/specs/2026-07-30-dispatch-via-cortex-design.md`.

**A box without Cortex gets no dispatch.** That is a real limitation, not an
oversight — both current boxes run it.

### 3. Any agent may consult any agent — on purpose

`scope` and `manager_visible` govern how a **human** reaches an agent: which
agents appear in their picker, and whether a direct session read is allowed.
Their job is to spare a user from working out which specialist to go to.

They are deliberately **not** a limit on which peers an agent may consult. A
chief of staff who could only reach the agents her human could already reach
would hand that burden straight back to the user, which is the opposite of why
she exists. So `list_teammates` returns the whole bench at every tier, and a
consult is never refused for reachability.

**What this means, stated plainly:** anyone who can reach an agent can reach,
through it, information held by every other agent on the box. Enabling dispatch
widens effective information access even though it changes no tier and no
scope. That is the intended trade — and it is bounded by consults being
**read-only**: `ask_teammate` returns an answer, it cannot send, publish, or
write. Actions stay behind their own human gate.

**It is recorded.** When the origin human's own tier would not have opened the
peer directly, the `governance_events` row carries
`findings[].beyond_human_reach = true`. Nothing is blocked; the point is that
"did anyone reach something through an agent that they could not reach
themselves?" stays an answerable question after the fact. Query on that field.

If an agent genuinely "cannot see" a teammate, it is not a scope problem —
check that the peer is in `AGENTS_JSON` at all, and that its model is
`fast`-class (see consult eligibility below).

Default is `off`. In `off` mode Cortex contributes **zero** dispatch tool
schemas and **zero** dispatch prompt text — an agent's context is byte-identical
to a box where this was never built. Pinned on both sides so a later refactor
cannot quietly regress it: `tests/test_dispatch_off_is_inert.py` here for the
server, and `tests/test_dispatch_isolation.py` in `ollie-hermes-cortex` for the
plugin surface (that one compares the off-mode prompt against the exact
pre-dispatch block, not a substring).

**Cortex's own memory and brain tools keep working no matter what dispatch
does.** That constraint outranks the feature: memory is load-bearing on both
boxes, dispatch is optional. Every seam is guarded, and each guard is verified
by deletion rather than by a green suite. If dispatch is misconfigured, broken,
or its module is unimportable, `memory_search` and the rest still answer.

**Plugins survive `hermes update` — confirmed 2026-07-30.** No re-install step
is needed, for either kind of plugin:

- `hermes plugins install` puts a plugin in `~/.hermes/plugins`, outside the
  `~/.hermes/hermes-agent` git checkout that `hermes update` pulls
  (`hermes_cli/plugins_cmd.py:76`).
- A *vendored* plugin inside the agent tree (like Cortex at
  `plugins/memory/cortex/`) is untracked there, and the update stashes with
  `git stash push --include-untracked` (`update_cmd.py:851`) and restores it.
  There is no `git clean` anywhere in the update path.

The `07-patch-cron-brain.sh` re-patch tax applies to **patches of tracked
upstream files**, which can conflict with an incoming pull — not to plugins. On
the JNOW box that is currently two files, `cron/scheduler.py` and `run_agent.py`.

One thing to leave alone: both boxes run
`updates.non_interactive_local_changes: stash`. Setting it to `discard` drops
the stash instead of restoring it, which **would** delete a vendored plugin.

(See the spike doc for full evidence.)

## Refusal reasons

Every non-grant response carries a `reason`. None of these are errors in the
plumbing sense — they're the tool telling the calling agent (and, through it,
the human) exactly why the ask didn't go through.

There are **two vocabularies**, and which one you're looking at tells you which
side refused. Server reasons come from `REASON_*` in `src/dispatch/types.py`
and mean the orchestrator considered the request and said no. Plugin reasons
are defined in `ollie-hermes-cortex/plugins/memory/cortex/dispatch.py` and mean
the request never reached the orchestrator at all — that code cannot import
from `src/` (it runs on a Hermes box where the orchestrator package isn't
installed), so it carries its own.

The two sets are deliberately **disjoint**, and a test asserts it. That is not
cosmetic: `dispatch is off` is reachable from either side, and if both used
`not_enabled` you could not tell "the orchestrator has dispatch off" from "this
profile has dispatch off" — which is exactly the two-environment mistake this
runbook opens by warning about.

### Server-side (the orchestrator decided)

| Reason | Meaning |
|---|---|
| `not_enabled` | Dispatch is off **on the orchestrator**, or the configured mode has no backend driver in this build (see "Modes" below). If you set `DISPATCH_MODE=direct` per profile and see this, you did not set it on the orchestrator — see "Enabling it" above. |
| `forbidden` | Either provenance could not be resolved (see below), or the request itself is invalid (empty question, a question over 4000 characters, an agent consulting itself). |
| `unknown_peer` | `to_agent` is not on the bench at all — check `AGENTS_JSON`. **Not** a permissions answer: the roster is no longer narrowed by the human's tier (see "Any agent may consult any agent" above), so this means the agent genuinely does not exist. |
| `peer_not_consult_eligible` | The peer exists, is reachable, but its model's `speed_class` isn't `fast` — see "Consult eligibility" below. |
| `cap_exceeded` | A consult to that peer for that human is already open (a chain re-entering a peer it is already inside), or the box-wide in-flight limit of 8 concurrent consults is reached. The detail text says which. See "Recursion is bounded server-side" below. |
| `timeout` | The peer's gateway didn't answer within the mediator's 30s window. |
| `peer_unavailable` | The peer's gateway could not be reached, or answered with something unusable. |
| `misconfigured` | The **orchestrator** isn't configured to dispatch: `HERMES_GATEWAY_KEY` is blank, or its app config couldn't be read. The peer was never contacted — don't go and check the peer's gateway, it's fine. |

### Plugin-side (the orchestrator was never reached)

| Reason | Meaning |
|---|---|
| `dispatch_off_locally` | **This profile** has `DISPATCH_MODE` unset or set to something other than `direct`. Distinct from the server's `not_enabled` on purpose — if you see this one, fix the *profile* env; if you see `not_enabled`, fix the *orchestrator* env. Note `local` and `linear` are valid mode names reserved for later slices and behave as off here, as does any typo. |
| `orchestrator_auth_failed` | The orchestrator answered 401/403. This profile's `ORCHESTRATOR_KEY` is unset, wrong, or was rotated without restarting the profile. |
| `orchestrator_unreachable` | Could not connect at all: nothing listening, wrong `ORCHESTRATOR_URL`, or the service is down. A timeout during the TCP handshake also lands here rather than on `orchestrator_timeout`. |
| `orchestrator_timeout` | Connected, but no answer within the client budget (75s, which exceeds the orchestrator's 60s worst case — owner lookup 10 + tier lookup 10 + gateway 30 + audit 10). |
| `orchestrator_error` | The orchestrator answered with some other HTTP status — 404, 500, anything not 401/403. The service is **up**; the route or the request is wrong. A 404 usually means the orchestrator is running a build without `/v1/dispatch/*`. |
| `dispatch_error` | The reply could not be read, or a tool was called with missing/unusable arguments. Covers a non-JSON body, a JSON body that isn't an object, and a reply whose shape doesn't match what the endpoint should return — the last of which usually means something *other than the orchestrator* is answering on `ORCHESTRATOR_URL`. |

## Recursion is bounded server-side

Two `fast`, `direct` agents can consult each other, and each level is a nested
synchronous completion holding a connection and a **paid** generation slot. The
`chain` field on a consult cannot stop this: it is asserted by the calling
agent process, which is exactly as trustworthy as an identity asserted by that
process, and in practice it is always empty because nothing carries it across
the gateway hop.

The orchestrator therefore tracks open consults itself
(`src/dispatch/inflight.py`) and refuses `cap_exceeded` when:

- that **(human, peer)** pair already has a consult open — a ping-pong between
  two agents has to re-enter one of them to keep going, so this cuts it at
  depth two; or
- **8 consults are already in flight** box-wide.

The pairwise guard is the one doing the anti-recursion work. The box-wide
ceiling is a backstop for a cascade that finds a path around it — a long ring
of distinct agents, or provenance resolving to different humans partway down.

That second bound is also, unavoidably, the box's consult concurrency budget:
a slot is held for the whole peer call, up to 30 seconds. On a busy box,
several people consulting at once can exhaust it, and the ninth simultaneous
consult refuses with `cap_exceeded` even though nothing is recursing. If that
shows up in practice, raise `_MAX_CONCURRENT_CONSULTS` in
`src/api/dispatch.py`. It is deliberately a separate constant from
`Caps.hop_cap`: that one bounds the depth of a single chain, this one bounds
how many consults run at once, and raising this one costs gateway generation
slots rather than loosening any chain-safety property.

Both conditions refuse with the same `cap_exceeded` reason but different
detail text, so the log line tells you which you hit.

## Consult eligibility comes from the model catalog

Only peers running a model whose `speed_class` is `fast` in `src/catalog.py`
can be consulted inline. `heavy` peers are still listed by `list_teammates`
(so the calling agent knows they exist) but any attempt to ask them refuses
with `peer_not_consult_eligible`. A model absent from the catalog has no
verified speed class at all and is therefore not eligible — this fails
closed the same way the rest of the design does. Practically: changing which
model a peer profile runs changes whether it can be consulted inline, with
no separate "is this agent consultable" list to keep in sync.

## Modes: only `direct` is implemented

`DISPATCH_MODE` accepts `off`, `direct`, `local`, and `linear` as valid
values — `local` and `linear` are reserved names for later slices. Setting
`DISPATCH_MODE` to anything other than `direct` (including `local` or
`linear`, and including typos, which fall back the same way) leaves the
**plugin** inert exactly as if it were `off`: no tools, no prompt text. If a
mode is a recognized name but has no backend wired up yet, the **API** still
refuses each consult it does receive with `not_enabled` rather than
erroring. Only `direct` has both a client-side tool path and a server-side
backend in this build.

## Provenance: fail-closed, and its limits

Provenance is *resolved*, not asserted. The plugin sends only its own agent
id and the Hermes session id it holds; the orchestrator looks up who owns
that `(agent_id, session_id)` pair in Supabase's `agent_sessions` table and
that row's `user_id` becomes the human the request acts for. There is no
field the caller can fill in to claim an identity directly — pydantic drops
any extra keys a caller sends on the consult body.

**If every consult on a box refuses with `forbidden`, check provenance
first.** The single most common cause is that the human (or something
acting as them) reached the agent directly instead of going through the
orchestrator's run-proxy. Single-ingress is what's supposed to guarantee a
matching `agent_sessions` row exists before dispatch ever sees the session —
if traffic bypassed the proxy, no row was ever written for that
`(agent_id, session_id)` pair, there is no owner to resolve, and
`resolve_origin` returns `None` for every request that uses it. This is the
expected symptom of the open provenance spike called out in the design: the
first real enablement on a live box is still the moment of truth for
whether this holds in practice, and a green test suite is not evidence that
it does.

**Session ids are global on a box, not scoped to a profile.** All Hermes
profiles on one box share a single session store, so the same session id
value can show up under more than one agent profile. That's exactly why
ownership is keyed on the **pair** `(agent_id, session_id)`, never on
`session_id` alone — a lookup that dropped the agent id would risk resolving
a completely different agent's owner for a session id that happens to
collide.

**What provenance does and doesn't prove.** `from_agent` — the other half of
that pair — is asserted by the calling plugin, not independently verified;
the plugin fills it in from its own profile's `DISPATCH_AGENT_ID`, and there
is no per-agent credential that could prove it. On top of that, the gateway
key (`HERMES_GATEWAY_KEY` / `ORCHESTRATOR_KEY`) is one shared secret across
every profile on a box, not a per-agent credential — so it doesn't gate
which agent id a caller can claim to be. A process holding that shared key
could, in principle, pair a session id with the wrong `agent_id` and cause
the lookup to resolve a different human's identity. This is inside the
design's accepted trust boundary (there's nothing more granular to check
against in this build), but it's the ceiling of what a resolved provenance
result actually proves — it is not a per-agent security boundary.

## Audit trail

Every consult attempt — grants and refusals alike — writes one row to
Supabase `governance_events` with `app = "dispatch"` and
`event_type = "dispatch_consult"`. The row records who asked whom, under
which human's tier, whether it was granted, and the refusal reason and
detail when refused. **The answer itself is deliberately never recorded** —
only the fact that the question was asked and what happened to it.

One gap to know about: a consult refused because provenance couldn't be
resolved (`forbidden`, no owner row found) does **not** produce a
`governance_events` row — there's no `Origin` yet to attribute it to, so it
can't be written in the same shape as every other refusal. It still leaves a
trace, just not in the audit table: the orchestrator logs a warning
(`dispatch: unresolvable provenance for <agent>/<session>`) for every such
attempt. If you're hunting for session-id probing or repeated provenance
failures, look in the service log, not `governance_events` — the audit
table alone will look artificially clean.

## The plugin's config schema is decorative — env vars are the only switch

`get_config_schema()` returns an empty list, on purpose. It previously
advertised `mode` and `orchestrator_url` keys, and **nothing read either of
them**: `_mode()` reads `DISPATCH_MODE` from the environment on every call, and
`DispatchHttpClient` reads `ORCHESTRATOR_URL` / `ORCHESTRATOR_KEY` from the
environment at construction. A host UI writing to those keys would have changed
nothing while appearing to work. Environment variables are the only live
switch in this build.

## Known limitation: the HTTP client reads its target once

`DispatchHttpClient` (the plugin's only outbound HTTP path) reads
`ORCHESTRATOR_URL` and `ORCHESTRATOR_KEY` once, at construction time. The
dispatch mode and the agent id, by contrast, are re-read from the
environment on every call. If a Hermes host builds one `DispatchProvider`
(and therefore one `DispatchHttpClient`) and reuses it across sessions, a
changed `ORCHESTRATOR_URL` or `ORCHESTRATOR_KEY` will not take effect until
that provider is rebuilt — mode and agent id changes take effect
immediately, but a moved orchestrator or a rotated key does not. Always
restart the profile after changing either of those two variables; don't
assume the running process picked it up.

## Verifying an enable worked

The teammates endpoint is provenance-gated, so a bare curl can no longer
enumerate the roster — that was the point of gating it. You need a real
`(agent_id, session_id)` pair that `agent_sessions` has an owner row for:

```sql
-- Supabase: a recent session for the agent you're testing
select agent_id, hermes_session_id, user_id
from public.agent_sessions
where agent_id = '<agent>'
order by last_active_at desc limit 5;
```

```bash
curl -s -H "Authorization: Bearer $ORCHESTRATOR_KEY" \
     "http://127.0.0.1:9123/v1/dispatch/teammates?agent=<agent>&session_id=<session>"
```

Expect `"ok": true` and a `teammates` list with each peer's `speed_class` and
`consult_eligible`. Remember the list is **that session's human's** roster, not
the full set of agents on the box — a shorter list than you expected usually
means the tier, not a dispatch fault. `"ok": false` with `forbidden` means the
`(agent_id, session_id)` pair has no owner row; `not_enabled` with an empty
list means the **orchestrator's** `DISPATCH_MODE` is still `off`.

Then, from the agent itself, ask a `fast` peer something trivial and confirm
the answer round-trips; ask a `heavy` peer the same question and confirm it
comes back `peer_not_consult_eligible` rather than silently succeeding or
hanging.
