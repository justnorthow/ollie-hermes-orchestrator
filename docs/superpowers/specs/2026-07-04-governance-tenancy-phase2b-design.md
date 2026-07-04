# Phase 2b — Instance-Scoped Governance Visibility (broker + configurable manager)

**Date:** 2026-07-04
**Status:** Approved design (brainstormed with John 2026-07-04)
**Parent:** `docs/superpowers/specs/2026-07-03-identity-consolidation-phase2a3-design.md` (Phase 2a.3) + the live Phase 2 sandbox rollout, which surfaced that `profiles.role='broker'` users exist and lost governance read under 2a.3's `0016` policy.
**Center of gravity:** ollie-hermes-orchestrator (governance RLS + orchestrator writes + whoami + admin API)
**Cross-repo impacts:** jnow-site (migration `0017`), ollie-hermes-frontend (Compliance nav/route gating via `useIdentity`)

---

## 1. Problem

Phase 2a.3's `0016` narrowed `governance_events` broad-read from the old
`user_role in ('broker','compliance')` list to `compliance`-tag OR
`user_role='admin'` OR own-email — on the assumption there were no `broker`
profiles. The live rollout disproved that: the brokerage business owner is a
`broker` (`profiles.role`), so they lost the governance oversight read they had
under `0005`. But the naive fix (re-add `user_role='broker'` to the policy) is
wrong: `governance_events` is a **single shared, instance-blind table** (every
box's orchestrator writes to it via the service role, no tenant column), so a
blanket `broker` grant would let **every** broker read **every** brokerage's
governance events — a cross-tenant leak.

The real requirement: a brokerage owner should see the governance/compliance
audit trail for **their own brokerage**, JNOW's own compliance staff should see
**all** brokerages (that is the governance *service* JNOW sells), and a
brokerage's manager should get that same brokerage-scoped view **only when
configured to** (noise for some managers, essential for others).

## 2. Decision

Make governance visibility **instance-scoped**, keyed on the same per-instance
authority model Phase 2a established (one box/instance = one brokerage;
`user_roles(instance_id, user_id, tier)` is the source of truth):

- Add `instance_id` to `governance_events`; the orchestrator stamps it from its
  `INSTANCE_ID` on every insert.
- Broad-read is granted per-instance by joining `user_roles`: `account_admin`+
  tier (the owner) OR an explicit per-user `governance_view` flag (the
  configurable manager) for *that event's* instance.
- The global `compliance` tag remains the cross-instance JNOW oversight grant
  (unchanged, all brokerages).
- Own-email remains the personal fallback.

This retires the legacy `user_role in ('broker','admin')` coupling entirely —
governance visibility now rides the per-instance tier/grant model, not the
JWT `user_role` claim. (`user_role` emission is still deferred for retirement,
as in 2a.3 — this phase simply stops *reading* it for governance.)

## 3. Data model (migration `0017_governance_tenancy.sql`, jnow-site)

Two additive changes to the shared Supabase project (`kpdqhntsvjzhqjeupzsj`):

```sql
-- 3a. instance tag on each governance event (nullable; historical rows stay null).
alter table public.governance_events add column if not exists instance_id text;
create index if not exists governance_events_instance_idx
  on public.governance_events (instance_id);

-- 3b. per-user, per-instance governance-view grant (for the configurable manager;
--     owners are covered by tier and never need this set).
alter table public.user_roles add column if not exists governance_view boolean not null default false;
```

Both are additive (`add column if not exists`, `default false`), so no backfill
of existing data is required and no existing behavior changes until §4 lands.

## 4. RLS — replace the `0016` policy (in the same `0017` migration)

```sql
drop policy if exists governance_events_select on public.governance_events;

create policy governance_events_select on public.governance_events
  for select to authenticated
  using (
    -- (a) JNOW cross-brokerage oversight: the global compliance tag.
    (auth.jwt() -> 'tags') ? 'compliance'
    -- (b) Your own brokerage: account_admin+ tier OR an explicit per-user
    --     governance grant for THIS row's instance. Tier is never a JWT claim
    --     (2a.3), so we read user_roles directly; the subquery is filtered to
    --     ur.user_id = auth.uid(), which user_roles' own select-own RLS allows.
    or exists (
      select 1 from public.user_roles ur
      where ur.user_id = auth.uid()
        and ur.instance_id = governance_events.instance_id
        and (ur.tier in ('account_admin', 'platform_operator') or ur.governance_view)
    )
    -- (c) Personal fallback: your own rows.
    or user_email = coalesce(auth.jwt() ->> 'email', '')
  );
```

Notes:
- The `exists` subquery joins `user_roles.instance_id = governance_events.instance_id`.
  A `null` `instance_id` (historical row) never matches, so clause (b) can't
  grant it — historical rows fall to (a)/(c) only. That is the intended clean
  cutover: pre-feature events are not retroactively assigned to a brokerage.
- The subquery runs under `user_roles`' select-own policy (it reads only
  `ur.user_id = auth.uid()` rows) — no `security definer` function needed.
- Performance: `user_roles` is tiny per user and PK-indexed on
  `(instance_id, user_id)`; the per-row `exists` is cheap. The
  `governance_events (instance_id)` index supports instance-filtered reads.

## 5. Orchestrator changes (ollie-hermes-orchestrator)

1. **Stamp `instance_id` on governance writes.** Every `governance_events`
   insert done by the orchestrator (the run-proxy guardrail events in
   `src/api/runs.py` — `guardrail.blocked`/`guardrail.flagged` — and the Gate-2
   attestation events in `src/api/guardrail.py`, plus any other governance_event
   POST) must include `"instance_id": cfg.instance_id`. Centralize the
   governance-event POST helper if it isn't already, so the column is added in
   one place. `instance_id` is `Config.instance_id` (already loaded, set on the
   box via `INSTANCE_ID`); if unset, write `null` (dev/no-instance).
2. **`whoami` returns `governanceView`.** Extend `GET /v1/whoami` (the handler
   in `src/api/…` that already returns `tier`/`label`/`tags`/`reachableAgentIds`)
   to also return `governanceView: bool` — true when the caller's `user_roles`
   row for `cfg.instance_id` has tier `account_admin`+ OR `governance_view=true`.
   Resolve it alongside the existing tier resolution (`src/api/roles.py`), fail
   closed to `false`.
3. **Admin verb to toggle the grant.** New `PUT /v1/admin/users/{user_id}/governance-view`
   in `src/api/admin.py`, gated `account_admin`+ via `authz.admin_denied`, body
   `{"enabled": bool}`. Sets `user_roles.governance_view = <enabled>` for
   `(cfg.instance_id, user_id)` via the service role (upserting the row if the
   user has no tier row yet — insert with tier `member`). Scoped to the caller's
   own instance (the admin API is per-instance already). Emit a governance/audit
   event for the change, consistent with the other admin writes (`role`/`tags`).
   Escalation note: `governance_view` is a visibility grant, not authority, so
   the tier-escalation guard used for `role` does not apply — an `account_admin`
   may set it for any user in their instance.

## 6. Frontend changes (ollie-hermes-frontend)

1. **`useIdentity` exposes `governanceView`.** Extend the hook
   (`src/hooks/useIdentity.ts`) and its `whoami` fetch to read the new
   `governanceView` boolean (default `false` on failure).
2. **Compliance nav + routes gate on it.** The Compliance / Verification /
   TRAIGA Report nav items (`src/components/Layout.tsx` `ALL_ITEMS`) and routes
   (`src/App.tsx` `RoleRoute`) currently gate on `minTier: account_admin` OR
   `anyTag: ['compliance']`. Add the `governanceView` flag as a third OR: visible
   when `account_admin+` OR `compliance`-tagged OR `governanceView`. A flagged
   manager then sees these pages; the RLS scopes the data to their brokerage.
   (`RoleRoute`/`NavItemRow` gain a `governanceView?: boolean` axis, OR read the
   flag from `useIdentity` directly — implementer's call, following the existing
   `minTier`/`anyTag` pattern.)
3. The Compliance page content needs **no** logic change — RLS does the
   per-instance filtering; the page just renders whatever rows come back.

## 7. Rollout

Additive column + flag → no data risk; the RLS change is a policy swap. Because
`governance_events` and its RLS are **shared** across boxes, ordering matters:

1. **Deploy the orchestrator first** (instance_id stamping) so new events start
   carrying `instance_id` *while the old `0016` policy is still active* (which
   ignores the column) — safe accumulation.
2. **Apply `0017`** (column + flag + new RLS). Now reads use `instance_id`; new
   events already have it.
3. **Deploy the frontend** (`governanceView` nav gate).

Do sandbox-first, then jnow, same as Phase 2. During the window where sandbox is
deployed but jnow is not, jnow's *new* events lack `instance_id` (its
orchestrator isn't stamping yet) → jnow brokers see governance only via
compliance-tag/own-email until jnow's orchestrator deploys. Not a leak, just
temporarily reduced visibility; keep the window short. Fold the smoke tests
(§8) into a `docs/runbooks/governance-tenancy-2b-rollout.md`. Rollback:
re-apply `0016`'s policy (from the 2a.3 runbook), revert orchestrator/frontend;
the added column/flag are inert and can stay.

## 8. Testing spine

- **RLS (SQL-level, in the migration's verification):**
  - An `account_admin` of instance X reads all `instance_id='X'` events, and NO
    `instance_id='Y'` events.
  - A `member` of X with `governance_view=true` reads all `X` events; with it
    `false`, reads only own-email rows.
  - A `compliance`-tagged user reads ALL instances' events.
  - A plain user reads only own-email rows; `instance_id=null` historical rows
    are visible only to compliance-tag/own-email.
- **Orchestrator:** a governance write carries `instance_id = cfg.instance_id`;
  `whoami` returns `governanceView=true` for an account_admin and for a flagged
  manager, `false` otherwise; `PUT …/governance-view` flips the flag (member
  tier still member) and is 403 for a member caller.
- **Frontend:** Compliance nav/route visible for account_admin, compliance-tag,
  and `governanceView=true`; hidden for a plain member.

## 9. Task decomposition (~5)

1. Migration `0017` (jnow-site): `instance_id` column + `governance_view` column
   + RLS policy swap, with SQL-level RLS verification.
2. Orchestrator: centralize the governance-event insert + stamp `instance_id`.
3. Orchestrator: `whoami` `governanceView` + `PUT /v1/admin/users/{id}/governance-view`
   (+ audit event) + tests.
4. Frontend: `useIdentity.governanceView` + Compliance/Verification/TRAIGA
   nav/route gating + tests.
5. Runbook `governance-tenancy-2b-rollout.md` (staged, sandbox-first, ordering
   from §7) + smoke tests.

## 10. Requirements traceability

| Requirement | Section |
|---|---|
| Brokerage owner sees their brokerage's governance | §4(b) via account_admin tier + §5.1 instance_id |
| JNOW compliance sees all brokerages | §4(a) compliance tag (unchanged) |
| Manager access configurable per-manager | §3b `governance_view` + §5.3 admin verb + §4(b) |
| No cross-tenant leak | §3a instance_id + §4 instance-scoped join |
| Retire legacy user_role='broker'/'admin' governance coupling | §4 |
| Owner never needs manual config | §4(b) tier clause |
| Additive / low-risk / reversible | §3, §7 |
