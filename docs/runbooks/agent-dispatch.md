# Agent-to-agent dispatch (`direct` mode)

Design: `docs/superpowers/specs/2026-07-30-switchable-dispatch-design.md`.

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

Then:

```bash
hermes plugins install   # installs plugins/dispatch/ into the profile
# restart that profile's gateway
```

### 3. Access control is the human's, not the agent's

Enabling dispatch does **not** widen who can reach which agent. The roster a
calling agent sees is filtered by `can_reach(tier, scope, manager_visible)` —
the same rule that decides what the dashboard shows that human and what a
direct session read allows. A peer the human could not reach directly is
absent from `list_teammates` and refuses as `unknown_peer`, identically to an
agent that does not exist. So if an agent "cannot see" a teammate you expect
it to see, check the **human's** tier and the peer's `scope` /
`manager_visible` in `AGENTS_JSON` first — not the dispatch config.

Default is `off`. In `off` mode the plugin contributes **zero** tool schemas
and **zero** system-prompt text — an agent's context is byte-identical to a
box that never had the plugin installed at all (`tests/test_dispatch_off_is_inert.py`
pins this so a later refactor can't quietly regress it).

**Re-install after `hermes update`.** Tool plugins installed via
`hermes plugins install` are expected to survive a `hermes update`, unlike
anything living in hermes-agent's bundled tree (which the update wipes) — but
this has not yet been observed across a real update on a live box. Treat
"plugin missing after update" as a known possibility until proven otherwise,
and re-run `hermes plugins install` if a post-update check shows it gone.

## Refusal reasons

Every non-grant response carries a `reason`. None of these are errors in the
plumbing sense — they're the tool telling the calling agent (and, through it,
the human) exactly why the ask didn't go through.

There are **two vocabularies**, and which one you're looking at tells you which
side refused. Server reasons come from `REASON_*` in `src/dispatch/types.py`
and mean the orchestrator considered the request and said no. Plugin reasons
are defined in `plugins/dispatch/reasons.py` and mean the request never reached
the orchestrator at all — the plugin cannot import from `src/` (it runs on a
Hermes box where the orchestrator package isn't installed), so it has its own.
The two sets are deliberately disjoint.

### Server-side (the orchestrator decided)

| Reason | Meaning |
|---|---|
| `not_enabled` | Dispatch is off **on the orchestrator**, or the configured mode has no backend driver in this build (see "Modes" below). If you set `DISPATCH_MODE=direct` per profile and see this, you did not set it on the orchestrator — see "Enabling it" above. |
| `forbidden` | Either provenance could not be resolved (see below), or the request itself is invalid (empty question, a question over 4000 characters, an agent consulting itself). |
| `unknown_peer` | `to_agent` is not on the roster **this human may reach**. Covers both "no such agent" and "an agent this human has no access to" — deliberately indistinguishable, so a refusal cannot confirm that an agent they can't see exists. |
| `peer_not_consult_eligible` | The peer exists, is reachable, but its model's `speed_class` isn't `fast` — see "Consult eligibility" below. |
| `cap_exceeded` | A consult to that peer for that human is already open (a chain re-entering a peer it is already inside), or the box-wide in-flight limit of 3 concurrent consults is reached. See "Recursion is bounded server-side" below. |
| `timeout` | The peer's gateway didn't answer within the mediator's 30s window. |
| `peer_unavailable` | The peer's gateway could not be reached, or answered with something unusable. |
| `misconfigured` | The **orchestrator** isn't configured to dispatch: `HERMES_GATEWAY_KEY` is blank, or its app config couldn't be read. The peer was never contacted — don't go and check the peer's gateway, it's fine. |

### Plugin-side (the orchestrator was never reached)

| Reason | Meaning |
|---|---|
| `orchestrator_auth_failed` | The orchestrator answered 401/403. This profile's `ORCHESTRATOR_KEY` is unset, wrong, or was rotated without restarting the profile. |
| `orchestrator_unreachable` | Could not connect at all: nothing listening, wrong `ORCHESTRATOR_URL`, or the service is down. |
| `orchestrator_timeout` | No answer within the plugin's client budget (75s, which exceeds the orchestrator's ~60s worst case). |
| `orchestrator_error` | The orchestrator answered with some other error status, or the response could not be parsed. The service is up; the route or the request is wrong. |

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
- **3 consults are already in flight** box-wide.

That second bound is deliberately conservative for a first enablement: on a
busy box with several people consulting at once, a legitimate fourth
simultaneous consult will refuse with `cap_exceeded`. If that shows up in
practice, the number is `Caps.hop_cap` in `src/dispatch/authority.py` — raise
it there rather than adding a new setting, and note that it is the same
constant the (currently unenforceable) chain-length hop cap uses.

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
