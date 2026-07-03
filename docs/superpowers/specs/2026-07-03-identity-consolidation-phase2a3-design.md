# Phase 2a.3 — Identity Consolidation (Tier + Functional Tags)

**Date:** 2026-07-03
**Status:** Approved design (brainstormed with John 2026-07-03)
**Parent:** Phase 2a RBAC (`2026-07-03-rbac-scope-taxonomy-phase2a-design.md`)
**Sequencing:** Build BEFORE 2a.2's nav-gating task; the orchestrator management proxy + edge block (2a.2) are independent.
**Center of gravity:** ollie-hermes-orchestrator + the Supabase migrations (jnow-site)
**Cross-repo impacts:** ollie-hermes-frontend (one identity hook, nav/route migration)

---

## 1. Problem

Two overlapping role systems exist:

- **Functional role** — `profiles.role` (`broker | agent | compliance | marketing`),
  read by the frontend `useUserRole` hook, propagated into the JWT `user_role`
  claim by a Supabase access-token hook, and load-bearing for `governance_events`
  RLS (`user_role in ('broker','compliance')`) + the Compliance/Verification/TRAIGA
  nav.
- **Permission tier** — `user_roles.tier` (`member | manager | account_admin |
  platform_operator`, Phase 2a), read by `whoami`, driving agent access + admin.

They are different axes ("what's your job" vs "what can you administer"), separately
plumbed, with two frontend gating vocabularies (`roles?: Role[]` vs the new tier).
This is confusing and duplicative.

## 2. Decision — tier is authority, tags are functional

- **Tier is the SOLE authority axis.** `user_roles.tier` alone decides every
  permission (agent access, admin, management). Nothing else grants authority.
- **Tags are a separate, non-authority attribute.** A new multi-value
  `user_tags` (`compliance`, `marketing`, …) gates feature/nav *visibility* that
  isn't about permission level (e.g. who sees Compliance pages). A tag never grants
  admin power: a `member` tagged `compliance` sees compliance features but can
  administer nothing; an `account_admin` has full authority regardless of tags.
- **`profiles.role` and the JWT `user_role` claim are retired as authority
  sources.** `user_roles` (tier) + `user_tags` (tags) become the single source of
  truth, both served by `whoami` and one frontend hook.

## 3. Data model — tags are GLOBAL, tier stays per-instance

**Key constraint (discovered from the auth hook):** the JWT is issued once per login
and is instance-blind (one cookie shared across `*.jnow.io`), and `governance_events`
is a single shared table. Therefore:

- **`tier` is per-instance and NEVER a JWT claim** — it is always resolved
  server-side by the orchestrator via `resolve_tier(instance_id, user_id)` (as Phase
  2a already does). Nothing about tier changes here.
- **Tags are GLOBAL** — a functional attribute of the person (a compliance officer is
  one regardless of which box), so they can safely ride the instance-blind JWT and
  back the global governance table.

New Supabase migration (`0013_user_tags.sql`) — **global** (no `instance_id`):

```sql
create table if not exists public.user_tags (
  user_id      uuid not null,
  tag          text not null,
  created_at   timestamptz not null default now(),
  primary key (user_id, tag)
);
alter table public.user_tags enable row level security;
-- SELECT own tags (defense in depth; orchestrator reads via service role;
-- the auth hook's supabase_auth_admin gets a permissive read policy like 0009 did
-- for profiles).
create policy user_tags_select_own on public.user_tags
  for select to authenticated using (user_id = auth.uid());
create policy user_tags_auth_admin_read on public.user_tags
  as permissive for select to supabase_auth_admin using (true);
-- No write policy → service-role (orchestrator admin API) writes only.
```

## 4. Data migration of `profiles.role`

A one-time, idempotent migration. `profiles.role` is global with DB values
`agent | compliance | marketing | admin` (verified in `0001_identity.sql` — note it
is NOT `broker`; the frontend `Role` type's `broker` never matched the DB).

- **Tags (global) — always applied:** `compliance` role → `user_tags(user_id,
  'compliance')`; `marketing` role → `user_tags(user_id, 'marketing')`.
- **Tier (per-instance) — applied per target instance:** `admin` →
  `user_roles(<instance>, user_id, 'account_admin')`; `agent`/`compliance`/`marketing`
  → `member`. Because `user_roles` needs an `instance_id` and `profiles` has none, the
  tier half is a **parameterized** step run once per instance_id (the runbook runs it
  for `sandbox`, then `jnow`). The tag half is global (runs once).

Both halves use `on conflict do nothing` so they never clobber a tier/tag already set
via the Phase 2a admin API. Documented in the runbook as an explicit, reviewed step
(it sets authority).

## 5. Auth hook + governance RLS (the risky, staged part)

The `custom_access_token_hook` (defined in `0001_identity.sql`) currently reads
`profiles.role` and stamps `user_role`. It only needs to additionally stamp the
GLOBAL `tags` array (tier is NOT stamped — it's per-instance, orchestrator-resolved).

Hook change: `tags` (jsonb array, from `user_tags` for the user, default `[]`),
emitted alongside the existing `user_role`. Grant `supabase_auth_admin` a read policy
on `user_tags` (mirrors 0009's fix for `profiles`).

**Staged for safety (a broken hook blocks all logins):**

1. **Additive first:** the hook emits `tags` WHILE STILL emitting `user_role` — no
   RLS change. Deploy + verify logins work and the `tags` claim appears.
2. **RLS cutover second, after the hook is proven:** `governance_events` RLS
   (`0005`) moves from `coalesce(auth.jwt() ->> 'user_role','') in
   ('broker','compliance')` to read the tag:
   `(auth.jwt() -> 'tags') ? 'compliance'
    OR (auth.jwt() ->> 'user_role') = 'admin'
    OR user_email = coalesce(auth.jwt() ->> 'email','')`.
   (Governance "see all" = compliance-tagged OR the global `admin` functional role;
   both are global, matching the shared/instance-blind governance table. Per-instance
   `account_admin` authority is deliberately NOT a governance-read grant — governance
   visibility is a functional/compliance concern, not a per-box admin one.)
3. **Retire `user_role` last (optional/deferred):** once nothing reads `user_role`,
   the old emission can be dropped. Since the `admin` functional value still usefully
   feeds governance step 2, retiring `user_role` is deferred until tags fully cover
   it — this phase keeps it.

Design the hook backward-tolerant: missing `user_tags` rows → `tags=[]`.

## 6. whoami extension

`GET /v1/whoami` returns `tags` alongside the existing fields:

```json
{ "userId": "...", "tier": "member", "label": "Member",
  "tags": ["compliance"], "reachableAgentIds": ["default"] }
```

`src/api/roles.py` gains `list_user_tags(user_id) -> list[str]` (GLOBAL — no
instance_id; cached, fail-closed to `[]`) and `set_user_tags(user_id, tags)` for the
admin API. `src/api/admin.py`'s `whoami` includes `tags`; `GET /v1/admin/users`
includes each user's tags; a `PUT /v1/admin/users/{id}/tags` sets them
(account_admin+, governance-audited). (The admin *UI* for tags is 2b; the API lands
here so the model is complete.)

## 7. Frontend: one identity hook

- **New `useIdentity() -> { tier, tags, reachableAgentIds, loading }`** backed by
  `whoami` (one cached fetch). Replaces the whoami calls scattered in main.tsx/picker
  and the separate `useUserRole`.
- **Nav/route gating migrates to it:** the nav item type gains `minTier?: Tier` and
  `anyTag?: string[]` (replacing `roles?: Role[]`). Management items → `minTier:
  'account_admin'`. Compliance/Verification/TRAIGA → `minTier: 'account_admin'` OR
  `anyTag: ['compliance']`. `RoleRoute`/`CapabilityRoute` route guards migrate to
  `useIdentity`.
- **Retire** `useUserRole`, the `Role` type, and the `profiles.role` read. Keep
  `useCapability` (backend feature flags — a genuinely separate axis).

## 8. Testing spine

- `list_user_tags` fail-closed to `[]`; cached; instance-scoped; invalidated on
  `set_user_tags`.
- `whoami` returns tags; a compliance-tagged member gets `tags:['compliance']`,
  `tier:'member'` (proving a tag grants no authority — reachableAgentIds still
  member-only).
- Migration mapping: broker→account_admin, agent→member, compliance→member+tag,
  marketing→member+tag; idempotent (`on conflict do nothing` doesn't clobber an
  admin-assigned tier).
- Auth hook (staged): after step 1, a login JWT carries `tier`+`tags` AND
  `user_role`; logins succeed. After step 2, governance RLS lets an account_admin OR
  a compliance-tagged user read all events, others their own.
- Frontend: `useIdentity` returns tier+tags; management nav gates on tier; compliance
  nav gates on tier-or-tag; a compliance-tagged member sees Compliance but not
  management; the old `useUserRole`/`Role` are gone (no remaining `profiles.role`
  read).

## 9. Rollout (staged, sandbox-first, folds into Phase 2)

1. Apply `0013` (user_tags) + the profiles.role data migration (reviewed).
2. Deploy the hook change **additive** (new claims alongside old); verify logins +
   claims on sandbox.
3. Deploy orchestrator (whoami tags + tag API) + frontend (useIdentity).
4. Cut over governance RLS to tier+tags; verify.
5. Retire the old `user_role` emission.
Rollback at each stage is independent (additive hook and RLS are separately
revertible). Add to the Phase 2 runbook.

## 10. Task decomposition (~7)

1. `0013_user_tags.sql` migration + profiles.role data migration.
2. `roles.py`: `list_user_tags` (cached, fail-closed) + `set_user_tags`.
3. `whoami` + admin API: return tags, `PUT /v1/admin/users/{id}/tags`.
4. Auth-hook change (additive: emit tier+tags, keep user_role) — locate + modify the
   hook; verify claims.
5. Governance RLS cutover to tier+tags (after hook proven).
6. Frontend `useIdentity` hook + nav/route migration + retire `useUserRole`/`Role`.
7. Retire the old `user_role` hook emission + runbook rollout steps.

## 11. Requirements traceability

| Requirement | Section |
|---|---|
| Tier = sole authority; tags = non-authority | §2 |
| user_tags table | §3 |
| profiles.role → tier+tag migration mapping | §4 |
| Auth hook derives tier+tags (staged, additive) | §5 |
| Governance RLS on account_admin OR compliance-tag | §5 |
| whoami returns tags; tag admin API | §6 |
| One useIdentity hook; retire useUserRole/Role | §7 |
| Staged, reversible rollout | §9 |
