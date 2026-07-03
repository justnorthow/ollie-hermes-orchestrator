# Phase 2a — RBAC Foundation + Scope Taxonomy + Enforcement

**Date:** 2026-07-03
**Status:** Approved design (brainstormed with John 2026-07-03)
**Parent spec:** `docs/superpowers/specs/2026-07-03-agent-instantiation-design.md` (§3 taxonomy, §7 RBAC)
**Center of gravity:** ollie-hermes-orchestrator
**Cross-repo impacts:** ollie-hermes-frontend (picker gating), jnow-site/jnow-workspace (migration)

---

## 1. Problem & scope

Phase 1 gave per-user session ownership but every authenticated user can still
reach every agent, and the orchestrator blindly trusts the JWT `X-Auth-Role`
claim (which defaults to `agent` and has no management surface). Phase 2
introduces a real role model and gates agent access by it.

This spec is **Phase 2a** — the foundation and enforcement:

- a canonical role model (fixed ordered tiers, customizable per-instance labels),
- a Supabase `user_roles` table as source of truth, resolved by the orchestrator,
- a `scope: user|company` taxonomy on agents,
- capability enforcement (the real 403 boundary) on the run-proxy and session
  endpoints,
- `GET /v1/whoami` so the frontend can gate its agent picker,
- admin **API** endpoints (list users, set role, get/set labels) — the contract
  the later UIs consume.

**Out of scope (own specs, fast follow):** 2b — the account-admin UI in the Ollie
frontend; 2c — the JNOW-operator user/role management in Fleet. This spec builds
the API and enforcement those UIs sit on; roles are seedable via SQL/curl until
2b/2c land.

## 2. Role model

**Four canonical tiers, fixed and ordered** (higher tiers strictly include lower
capabilities):

| Tier (canonical, stored) | Default label | Capabilities (v1) |
|---|---|---|
| `member` | "Member" | Chat with `scope: user` agents (their Ollie) |
| `manager` | "Manager" | + reach the configured company-agent subset; team audit visibility (audit surfaces in a later phase — the tier exists now) |
| `account_admin` | "Account Admin" | + manage users & roles in their instance; all company agents; full audit |
| `platform_operator` | "JNOW Operator" | + everything, cross-instance; assignable only via Fleet (2c) |

- **Capabilities are fixed in code per tier** (v1 boundary — a runtime-editable
  permission matrix is explicitly deferred). The tier→capability mapping is
  written to be *extended* (new capabilities added in code) without being
  *runtime-editable*.
- **Labels are per-instance customizable** and purely cosmetic. Enforcement never
  reads a label; it reads the canonical tier. A brokerage may relabel to
  "Agent / Team Lead / Broker-Owner", a clinic to "Staff / Office Manager /
  Practice Owner".
- **Default tier** for a user with no `user_roles` row = `member` (safe floor,
  fail-closed).

## 3. Data model

New Supabase migration (`user_roles` + `role_labels`):

```sql
create table if not exists public.user_roles (
  instance_id  text not null,
  user_id      uuid not null,
  tier         text not null check (tier in ('member','manager','account_admin','platform_operator')),
  assigned_by  uuid,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  primary key (instance_id, user_id)
);
alter table public.user_roles enable row level security;
-- SELECT: a user may read their own row; account_admin+/service role read all.
-- Writes are service-role only (the orchestrator admin API is the writer).
create policy user_roles_select_own on public.user_roles
  for select to authenticated
  using (user_id = auth.uid());

create table if not exists public.role_labels (
  instance_id  text not null,
  tier         text not null check (tier in ('member','manager','account_admin','platform_operator')),
  label        text not null,
  primary key (instance_id, tier)
);
alter table public.role_labels enable row level security;
create policy role_labels_select_all on public.role_labels
  for select to authenticated using (true);   -- labels are non-sensitive display text
```

- `instance_id` scopes rows per customer box and is **required, not cosmetic**:
  the sandbox and jnow boxes share one Supabase project (`kpdqhntsvjzhqjeupzsj`),
  so without it a user's sandbox role would leak into prod. The orchestrator
  reads its own id from a new `INSTANCE_ID` env var (Fleet-set per box; a stable
  sentinel like `"sandbox"` / `"jnow"` for the current two boxes) and uses it for
  every `user_roles` / `role_labels` lookup and write. Phase 1's `agent_sessions`
  can adopt the same env later; for now it is self-contained to Phase 2a.
- The manager company-agent subset (which company agents a `manager` may reach)
  is **agent-config**, not a per-user grant: agents may declare
  `manager_visible: true`. Keeps v1 simple — no per-user agent ACL table.

## 4. Orchestrator: role resolution

New module `src/api/roles.py`:

- `resolve_tier(user_id: str) -> str` — reads `user_roles` for the configured
  instance; returns the stored tier or `"member"` if absent/unavailable
  (fail-closed). Short-TTL in-process cache (e.g. 30s) keyed by `user_id` so a
  role edit takes effect within the TTL without a per-request DB hit on every
  call. Best-effort: any store error → `"member"`.
- `tier_rank(tier) -> int` — canonical ordering for "≥ tier" checks.
- `capabilities(tier) -> Capabilities` — the fixed in-code mapping.
- `labels() -> dict[tier, str]` — reads `role_labels`, falling back to the
  default label per tier.

The orchestrator resolves the caller from `X-Auth-User-Id` (already threaded in
Phase 1). The JWT `X-Auth-Role` claim is **no longer trusted for authorization**
(it may remain for governance-event annotation); `user_roles` is authoritative.

## 5. Agent scope taxonomy

`AGENTS_JSON` entries gain two optional fields:

- `scope: "user" | "company"` — default `"company"` when omitted (safe: unmarked
  agents stay admin-gated). Ollie is explicitly `"user"`.
- `manager_visible: true | false` — default `false`; when true a `manager` tier
  may reach this company agent. Ignored for `scope: user` agents.

Threaded through `src/agents_json.py` (parser), the `Agent` model
(`src/models.py`), and `_entry_to_agent` (`src/api/agents.py`), and consumed by
the frontend via `parseAgents` (`src/config.ts`).

## 6. Enforcement (the real boundary)

A single helper `can_reach(tier: str, agent_scope: str, manager_visible: bool) -> bool`:

- `scope == "user"` → any authenticated tier may reach (≥ `member`).
- `scope == "company"`:
  - `account_admin`+ → yes
  - `manager` → yes iff `manager_visible`
  - `member` → no

Applied fail-closed, **before Hermes/gateway is touched**, in:

- `create_run` and `run_events` / stop / approval / pending (run-proxy, `runs.py`)
- the session endpoints (`sessions.py`) — a member cannot list/read/delete a
  company agent's sessions even by direct call.

Denied → `403 {"detail": "Forbidden"}` (does not leak whether the agent exists;
distinct from Phase 1's `"Session not found"` which is about thread ownership).
Identity-less internal bearer callers (no `X-Auth-User-Id`) remain inside the
trust boundary and skip the check (same rule as Phase 1).

The Phase 1 ownership gate is unchanged and independent: RBAC decides "may you
talk to this agent," ownership decides "is this your thread." A request must pass
both.

## 7. `GET /v1/whoami`

Returns, for the authenticated caller:

```json
{
  "userId": "<uuid>",
  "tier": "member",
  "label": "Member",
  "reachableAgentIds": ["default"]
}
```

Derived server-side from `resolve_tier` + each agent's scope/`manager_visible`.
The SPA renders the agent picker from `reachableAgentIds` only — a member sees
just Ollie, collapsing the picker to a single conversation. This is UX only; §6
is the enforcement. Identity-less/unauthenticated → 401.

## 8. Admin API (contract for 2b/2c)

All gated to `account_admin`+ (a lower tier → `403`), enforced by `resolve_tier`:

- `GET /v1/admin/users` → the instance's users with their tiers and labels.
  (User identity/email comes from the Supabase admin API via the service role;
  the orchestrator joins it with `user_roles`.)
- `PUT /v1/admin/users/{user_id}/role` — body `{"tier": "manager"}`; validates
  the tier; writes `user_roles` (records `assigned_by`); a `platform_operator`
  tier is assignable **only** by a `platform_operator` (an `account_admin`
  cannot mint one). Returns the updated row.
- `GET /v1/admin/role-labels` → the instance's tier→label map (defaults merged).
- `PUT /v1/admin/role-labels` — body `{"manager": "Team Lead", ...}`; writes
  `role_labels`; labels are free text, length-bounded, never affect enforcement.

Every admin write emits a governance event (reusing the Phase 1/ TRAIGA
`governance_events` writer) for auditability — role changes are security-relevant.

## 9. Rollout ordering (fail-closed seeding)

Because an empty `user_roles` table resolves everyone to `member` (which hides
company agents from admins too), sequence the deploy like Phase 1's backfill:

1. Apply the migration.
2. Seed `platform_operator`/`account_admin` rows for the operator + John
   (SQL or the admin API via curl with a temporarily-elevated seed).
3. Deploy the orchestrator (resolution + enforcement + admin API).
4. Deploy the frontend (whoami-driven picker).
5. Smoke-test tiers on-box before the public hostname.

Detailed steps go in a runbook at plan time, sandbox-first.

## 10. Testing spine

- No role row → `resolve_tier` = `member`.
- `member` → `scope: company` agent run/session = `403`; → `scope: user` = allowed.
- `manager` → `manager_visible` company agent = allowed; non-visible = `403`.
- `account_admin` → all agents.
- Non-admin → any `/v1/admin/*` = `403`; `account_admin` cannot set
  `platform_operator`.
- `whoami` returns exactly the reachable set per tier; label changes don't move
  any enforcement decision.
- Phase 1 ownership still independently enforced (RBAC-allowed agent + foreign
  session = still `403` not-found).
- Cache: a role change is reflected within the TTL.
- Existing suites stay green; Hermes untouched.

## 11. Requirements traceability

| Requirement (brainstorm) | Section |
|---|---|
| Roles table, orchestrator reads it (instant edits) | §3, §4 |
| Member / Manager / Account Admin / (Operator) tiers | §2 |
| Customizable role labels, fixed capabilities (v1) | §2, §8 |
| Agent scope taxonomy user\|company | §5 |
| Member → user-scoped only, 403 on company agents | §6 |
| Frontend learns its role to gate the picker | §7 |
| Admin API for the two future UIs | §8 |
| More than just agent access (extensible capabilities) | §2, §4 (`capabilities()`), §8 |
