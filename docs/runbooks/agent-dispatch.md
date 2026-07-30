# Agent-to-agent dispatch (`direct` mode)

Design: `docs/superpowers/specs/2026-07-30-switchable-dispatch-design.md`.

## What it does

One agent asks another agent a question and gets the answer back in the same
turn — `ask_teammate` in, an answer out, nothing queued. It **cannot** give
anyone work. Task/queue dispatch (`assign_task`, `check_assignments`, the
`dispatch_tasks` table, the heartbeat sweep) is a later slice and does not
exist yet; if you find yourself wanting an agent to hand off a task rather
than ask a question, that isn't built.

## Enabling it

Per profile, in that profile's environment:

```bash
DISPATCH_MODE=direct
DISPATCH_AGENT_ID=<this-agent's-id>
```

Then:

```bash
hermes plugins install   # installs plugins/dispatch/ into the profile
# restart that profile's gateway
```

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

| Reason | Meaning |
|---|---|
| `not_enabled` | Dispatch is off on this instance, or the configured mode has no backend driver in this build (see "Modes" below). |
| `forbidden` | Either provenance could not be resolved (see below), or the request itself is invalid (empty question, an agent consulting itself). |
| `unknown_peer` | `to_agent` is not on this box's roster. |
| `peer_not_consult_eligible` | The peer exists but its model's `speed_class` isn't `fast` — see "Consult eligibility" below. |
| `cap_exceeded` | The chain would cycle back to an agent already in it, or the hop cap (3) was reached. |
| `timeout` | The peer's gateway didn't answer within the mediator's timeout window. |
| `peer_unavailable` | The peer's gateway could not be reached at all. |

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

```bash
curl -s -H "Authorization: Bearer $ORCHESTRATOR_KEY" \
     "http://127.0.0.1:9123/v1/dispatch/teammates?agent=<agent>"
```

Expect a `teammates` list with each peer's `speed_class` and
`consult_eligible`. Then, from the agent itself, ask a `fast` peer something
trivial and confirm the answer round-trips; ask a `heavy` peer the same
question and confirm it comes back `peer_not_consult_eligible` rather than
silently succeeding or hanging.
