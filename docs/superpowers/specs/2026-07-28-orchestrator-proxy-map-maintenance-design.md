# Orchestrator-maintained proxy maps

Date: 2026-07-28
Base: `0d8caf5` (main)
Status: approved for implementation

## Problem

`HERMES_GATEWAY_URLS` and `HERMES_DASHBOARD_URLS` in `~/.config/ollie-orchestrator/.env`
map an agent id to its loopback gateway/dashboard URL. They are written **only** by
`03-install-profile.sh` / `05-install-orchestrator.sh` at provision time, via
`detect_agents | render-proxy-maps.py` in the install repo.

Nothing re-renders them when an agent is created later. So an agent created through
the dashboard UI or `ollie-fleetctl` is absent from both maps, and
`check-box-config.sh` reports the box as not done-done:

```
FAIL: HERMES_GATEWAY_URLS incomplete (missing: <agent>)
FAIL: HERMES_DASHBOARD_URLS incomplete (missing: <agent>)
```

This has now occurred twice in two days: the Towns box on 2026-07-27, and the
eSource/GetBilled box on 2026-07-28 after `mail-agent` was created. Both were
repaired by hand with the same three commands.

**Chat is not affected.** `agents_json.loopback_url_for()` (`src/agents_json.py:151`,
added after the prod `pam` incident on 2026-07-17) derives the URL from `AGENTS_JSON`,
which create/delete maintains synchronously. Lookups fall back correctly rather than
503-ing. The impact is a persistently dirty gate and an `.env` that misdescribes the box.

The sibling defect — a UI-created dashboard unit never receiving its
`session-token.conf` drop-in — was fixed in `0d8caf5`. This spec covers only the maps.

## Non-goals

- Changing how the maps are consumed. `_gateway_base` / `_dashboard_base` and the
  `loopback_url_for()` fallback stay exactly as they are.
- Relaxing `check-box-config.sh`. Teaching the gate to accept the `AGENTS_JSON`
  fallback was considered and rejected: it tolerates drift rather than repairing it,
  leaves the `.env` stale, and softens a check that caught a real prod incident.
  Startup reconcile (below) makes it unnecessary.
- Retiring the maps in favour of `AGENTS_JSON`. They remain the operator override
  channel.

## Design

### Surgical operations, not re-render

`render-proxy-maps.py` keeps an existing map value when it is valid JSON covering every
detected id, and otherwise regenerates the whole thing. That rule is correct for
provision time and wrong when driven per-operation:

- **Delete would leave a corpse.** After removing an agent the map still covers every
  *remaining* id, so it is "kept" and the dead agent's entry survives, pointing at a
  freed port. Recreating an agent under that name on different ports would let the
  stale entry win over `loopback_url_for()` — a silent misroute, worse than the clean
  503 we have today.
- **Regeneration would clobber operators.** The non-"kept" branch rewrites the map
  wholesale, discarding any entry an operator deliberately pinned.

So each operation touches only the one agent id in play:

- **create** — add the entry **only if the id is absent**. Absent is precisely what the
  gate flags, so this closes it. An id an operator has already pinned is left alone,
  preserving the "operator entries win" property from the 2026-07-17 fix.
- **delete** — remove that id. No corpse.

Everything else in the file, operator extras included, is untouched.

### Startup reconcile (self-healing)

On startup the orchestrator reads every agent from `AGENTS_JSON` and adds any that are
missing from the two maps, under the same add-if-missing rule. Never overwrites, never
removes; corpse removal remains exclusive to explicit delete.

This repairs drift rather than tolerating it, covers agents created by any older
orchestrator on any box, and — because `05-install-orchestrator.sh` restarts the
service — means rollout itself heals every box, including drift not yet discovered.

### Module

New `src/proxy_maps.py`, single purpose: keep the orchestrator's own `.env` map keys
covering known agents.

```python
def upsert_agent(env_path, agent_id, *, gateway_port, dashboard_port) -> None
def remove_agent(env_path, agent_id) -> None
def reconcile_all(env_path, agents) -> list[str]   # ids added, for logging
```

`agents` is the `AgentEntry` list returned by `agents_json.read_agents()`, so
`reconcile_all` takes already-parsed state and performs no file reads of its own beyond
the target `.env`.

Writes go through the existing atomic upsert `set_env_key()` (`src/agents_json.py:124`)
rather than introducing a second env writer.

`Config` gains `orch_env_path`, defaulting to `~/.config/ollie-orchestrator/.env` and
honouring `ORCH_ENV` — the same variable `render-proxy-maps.py` reads, so both point at
one file.

### Wiring

- **create** — called inside the existing `update_agents_json` step in `lifecycle.py`.
  Deliberately **not** a new SSE step: the frontend's create modal has eight hardcoded
  steps, and adding a ninth introduces frontend/orchestrator skew of exactly the kind
  already suspected in the hung-modal behaviour.
- **delete** — mirror call alongside the existing `AGENTS_JSON` removal.
- **startup** — `reconcile_all` once from the FastAPI startup path in `src/api/main.py`,
  after `Config` is loaded and attached to `app.state`.

### Failure handling

Best-effort throughout: log a warning and continue, mirroring
`write_session_token_dropin` returning `False` rather than raising.

- A fully built, running agent must never be rolled back because an `.env` write failed.
- An `.env` write must never prevent the service from starting.

In both cases `loopback_url_for()` keeps routing correct, so the worst case degrades to
exactly today's behaviour.

### Deliberately excluded

**Mutating `os.environ` after a write.** Startup reads the environment before the file
is rewritten, so the process that repairs the maps still uses the old ones until its
next restart. The file is correct immediately; the process catches up later. Syncing
live env would buy no behavioural change — the fallback already resolves every lookup —
while adding a second source of truth to reason about during debugging.

## Testing

`tests/test_proxy_maps.py`:

- adds a missing id on create
- leaves an operator-pinned id untouched
- removes the id on delete
- regenerates from a malformed or absent value
- writes atomically
- `reconcile_all` adds only missing ids, is idempotent across repeated boots
- a failure inside `reconcile_all` does not raise into startup

Plus `lifecycle` coverage asserting create and delete call through.

## Rollout

Deploy `bash scripts/05-install-orchestrator.sh` on GetBilled, jnow prod, sandbox, and
Towns. All four are behind `0d8caf5`, so they pick up the session-token fix in the same
pass. Confirm `check-box-config.sh` reports done-done on each.

## Known limitation

Reconcile trusts `AGENTS_JSON` as the source. An agent absent from *both* `AGENTS_JSON`
and the maps will not be resurrected — that is a re-provision case, and is left as a
loud failure rather than silently papered over.
