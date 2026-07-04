# Roll out identity consolidation — tier + global tags (Phase 2a.3)

Ships the collapse of two overlapping role systems into one: `user_roles.tier`
remains the sole authority axis (Phase 2a), and a new GLOBAL `user_tags` table
(`compliance`, `marketing`, …) becomes the sole source for functional/visibility
tags — replacing `profiles.role` and the JWT `user_role` claim as the thing the
frontend and governance RLS key off of. A tag never grants authority: a
`member` tagged `compliance` sees Compliance nav but can administer nothing;
an `account_admin` can reach everything regardless of tags. Design:
`docs/superpowers/specs/2026-07-03-identity-consolidation-phase2a3-design.md`.

Roll out to the **sandbox box first** (`ollie@178.105.216.167`,
`olliesandbox.jnow.io`). Only move to `jnow` (`ollie.jnow.io`) after every
sandbox check below passes.

**Read this before starting — two hard orderings, not just "apply in numeric
order":**

1. **`0014` (tag seeding) MUST land before `0016` (governance RLS cutover).**
   `0016` grants governance broad-read to compliance-*tagged* users. If you
   cut over the RLS policy before `0014` has copied `profiles.role='compliance'`
   users into `user_tags`, every legacy compliance user loses governance
   access the instant `0016` applies (the `admin`-functional-role and
   own-email fallbacks in `0016`'s policy do NOT cover them). `0014` seeds the
   tag; `0016` is the only thing that reads it for governance. Do `0014`
   first, full stop.
2. **`0015` (hook) must be LIVE and logins verified before `0016` (RLS).**
   `0016`'s policy reads `auth.jwt() -> 'tags'`. Until `0015` is deployed and a
   fresh login has actually stamped a `tags` claim into the JWT, that claim
   doesn't exist — `0016` would silently deny everyone the tag-based grant
   (falling through to the `admin`/own-email conditions only). Worse, `0015`
   itself replaces the login-critical `custom_access_token_hook` function — a
   broken hook blocks **all logins on every box sharing this Supabase
   project**. `0015` is deployed additively (keeps stamping `user_role`
   alongside the new `tags`) specifically so a mistake here is cheap to
   detect and revert. The LOGIN GATE below is not optional busywork — it is
   the only thing standing between "additive, reversible change" and
   "nobody can log into any Ollie instance."

Migrations live in the jnow-workspace repo at
`development/core/supabase/migrations/`, applied via the Supabase SQL editor
against the shared project ref **`kpdqhntsvjzhqjeupzsj`** (same project for
both boxes — each migration only needs to be applied once).

Before starting, record the pre-rollout orchestrator SHA on each box —
`git rev-parse HEAD` in `~/ollie-hermes-orchestrator` — Rollback depends on it.

## 1. Apply `0013` (user_tags table) + `0014` (tag data migration)

1. Supabase dashboard for `kpdqhntsvjzhqjeupzsj` → **SQL Editor**.
2. Paste and run the full contents of `0013_user_tags.sql`. Confirm in
   **Table Editor**: `public.user_tags` exists (columns `user_id`, `tag`,
   `created_at`; primary key `(user_id, tag)`; RLS enabled with
   `user_tags_select_own` and a permissive `user_tags_auth_admin_read` for
   `supabase_auth_admin`). No write policy — only the service-role key
   (the orchestrator's admin API) can insert.
3. Paste and run the full contents of `0014_migrate_profiles_tags.sql`. This
   is the GLOBAL tag half of the profiles.role migration — it copies
   `profiles.role = 'compliance'` → `user_tags(user_id, 'compliance')` and
   `profiles.role = 'marketing'` → `user_tags(user_id, 'marketing')`, using
   `on conflict (user_id, tag) do nothing` so it never clobbers a tag already
   set via the admin API. Runs once — global, not per-instance.
4. Confirm: **Table Editor** → `public.user_tags` now has one row per legacy
   compliance/marketing user. Spot-check counts against `profiles`:
   ```sql
   select role, count(*) from public.profiles where role in ('compliance','marketing') group by role;
   select tag, count(*) from public.user_tags group by tag;
   ```
   The two counts per tag should match.

This only needs to happen once — it's the same shared project for both boxes.

## 2. Seed tiers per instance (parameterized — the migration's other half)

`user_roles` needs an `instance_id` that `profiles` doesn't have, so the tier
half of the profiles.role migration is a parameterized runbook step, run once
per instance, rather than a plain migration file. Maps `profiles.role`:
`admin` → `account_admin`, everything else (`agent`/`compliance`/`marketing`)
→ `member`.

In the Supabase SQL editor, for the **sandbox** box:

```sql
insert into public.user_roles (instance_id, user_id, tier)
  select 'sandbox', user_id, case when role = 'admin' then 'account_admin' else 'member' end
  from public.profiles
on conflict (instance_id, user_id) do nothing;
```

`on conflict (instance_id, user_id) do nothing` preserves any tier already
set via the Phase 2a admin API — including John's seeded `platform_operator`
row (this insert will try to set him to `account_admin` from his `admin`
profiles.role, but the conflict clause means his existing higher tier wins;
no manual guard needed). Confirm:

```sql
select tier, count(*) from public.user_roles where instance_id = 'sandbox' group by tier;
```

Repeat with `'jnow'` in place of `'sandbox'` when you reach Step 6 (the jnow
box) — do not run the `jnow` variant yet.

## 3. Apply `0015` (hook additive) — LOGIN GATE

`0015_hook_emit_tags.sql` replaces `custom_access_token_hook` to ALSO stamp a
GLOBAL `tags` claim (a JSON array from `user_tags`, `[]` if the user has no
rows) alongside the existing `user_role` claim — `user_role` emission is
unchanged, nothing is removed yet.

1. Supabase SQL editor → paste and run the full contents of
   `0015_hook_emit_tags.sql`.
2. **Immediately** — do not do anything else first — log into
   `https://olliesandbox.jnow.io` (a fresh login, so a new token is minted;
   an already-open session's existing token won't have the new claim).
3. Confirm login succeeds. If it does not, this is the rollback trigger —
   see Rollback (Step 7) for `0015` now, before touching anything else.
4. Confirm the fresh token carries a `tags` claim. This is the authoritative
   check for the hook itself — do this even if Step 4's orchestrator/whoami
   deploy hasn't happened yet, since `whoami` (once deployed) reads
   `user_tags` directly via the service role rather than from the JWT, so it
   doesn't prove the *hook* is stamping the claim.
   - In the browser console, after logging in, find and decode the Supabase
     access token (stored in `localStorage`, key matches
     `sb-<project-ref>-auth-token`):
     ```js
     const { access_token } = JSON.parse(localStorage.getItem('sb-kpdqhntsvjzhqjeupzsj-auth-token')).currentSession ?? JSON.parse(localStorage.getItem('sb-kpdqhntsvjzhqjeupzsj-auth-token'));
     JSON.parse(atob(access_token.split('.')[1]));
     ```
     (the exact `localStorage` key/shape can vary by supabase-js version —
     if the above doesn't match, open Application → Local Storage in
     devtools, find the `sb-*-auth-token` entry, copy the `access_token`
     value, and decode it at [jwt.io](https://jwt.io) instead.)
   - Confirm the decoded payload has `tags` (an array, e.g. `[]` or
     `["compliance"]`) and still has `user_role` too.
5. **Do NOT proceed to Step 5 (`0016`) until logins are proven on this box.**
   If login is broken: roll back immediately by re-applying `0001`'s original
   hook body (no `tags` stamping) in the SQL editor:
   ```sql
   create or replace function public.custom_access_token_hook(event jsonb)
   returns jsonb
   language plpgsql
   as $$
   declare
     claims jsonb;
     found_role text;
   begin
     select role into found_role from public.profiles where user_id = (event->>'user_id')::uuid;
     claims := event->'claims';
     claims := jsonb_set(claims, '{user_role}', to_jsonb(coalesce(found_role, 'agent')));
     event := jsonb_set(event, '{claims}', claims);
     return event;
   end;
   $$;

   grant execute on function public.custom_access_token_hook to supabase_auth_admin;
   revoke execute on function public.custom_access_token_hook from authenticated, anon, public;
   ```
   Re-verify login succeeds, then stop and diagnose before retrying `0015`.

## 4. Deploy orchestrator + frontend

Ships `whoami`/admin-API tags (`src/api/admin.py`) and the frontend's single
`useIdentity` hook (tier+tags), replacing the old `useUserRole`/`Role`
role-gating.

Orchestrator, on the box:

```bash
cd ~/ollie-hermes-orchestrator
git pull origin master
systemctl --user restart ollie-orchestrator
```

Verify `whoami` now returns `tags`:

```bash
curl -s -H "Authorization: Bearer $ORCHESTRATOR_KEY" \
     -H "X-Auth-User-Id: 1a2b341c-0d01-418f-9fdb-4cebc27058c7" \
     http://127.0.0.1:9123/v1/whoami
```

Expect `200` with `tier`, `label`, `reachableAgentIds`, and now `tags` (an
array — `[]` unless John has a tag row). `1a2b341c-0d01-418f-9fdb-4cebc27058c7`
is John's Supabase user UUID.

Frontend image — tag off the current image before touching it, following the
same `:rollback-pre-<feature>` convention as prior rollouts, using a **new**
tag so you never clobber a tag prod may still be sharing:

```bash
docker tag justnorthow/ollie-hermes-frontend:latest \
           justnorthow/ollie-hermes-frontend:rollback-pre-identity
```

Rebuild and push `justnorthow/ollie-hermes-frontend:latest` from the frontend
repo, tagging this rollout's build `:identity-2a3` (the branch this shipped
from). On the box:

```bash
docker compose pull
docker compose up -d
```

Verify in the UI at `https://olliesandbox.jnow.io`:

- Log in as John (`account_admin`/`platform_operator`) — Management,
  Compliance, Verification, and TRAIGA Report nav items are all visible.
- Log in as a throwaway `member` user with no tags — Management, Compliance,
  Verification, and TRAIGA Report are all hidden.
- Log in as a throwaway `member` user tagged `compliance` (set via
  `PUT /v1/admin/users/{user_id}/tags`, see below) — Compliance,
  Verification, and TRAIGA Report are visible; Management is still hidden.
  This is the key proof that tags gate visibility without granting authority.

To tag a throwaway user for that last check:

```bash
curl -s -X PUT -H "Authorization: Bearer $ORCHESTRATOR_KEY" \
     -H "X-Auth-User-Id: 1a2b341c-0d01-418f-9fdb-4cebc27058c7" \
     -H "Content-Type: application/json" -d '{"tags":["compliance"]}' \
     http://127.0.0.1:9123/v1/admin/users/<throwaway-user-uuid>/tags
```

Expect `200` with `{"userId": "<throwaway-user-uuid>", "tags": ["compliance"]}`.

At this point the single `useIdentity` hook is driving all nav/route gating
in the frontend — there is no remaining `useUserRole`/`profiles.role` read in
the UI. `user_role` is still being emitted in the JWT (Step 3 was additive)
and governance RLS has not changed yet — that's Step 5.

## 5. Apply `0016` (governance RLS cutover) — only after Steps 1–4 verified

`0016_governance_rls_tags.sql` moves `governance_events`' broad-read policy
off the old `user_role in ('broker','compliance')` list and onto tags: a
compliance-tagged user (via the JWT `tags` claim), OR the global `admin`
functional role (still read from `user_role` — retiring that emission is
explicitly deferred, not part of this phase), OR the row's own email, may
read it. Do not apply this until `0014` has run on this project (Step 1) and
`0015`'s `tags` claim is proven live (Step 3) — both are hard prerequisites,
not just numeric ordering.

Supabase SQL editor → paste and run the full contents of
`0016_governance_rls_tags.sql`.

Verify:

- **Compliance-tagged (or `admin`) user reads all events:** as John (has
  `user_role = 'admin'` from `profiles`, satisfying the second OR-clause) or
  as the throwaway compliance-tagged member from Step 4, load the Compliance
  page (or query directly) — all `governance_events` rows are visible, not
  just their own.
- **A plain member reads only their own:** as a throwaway `member` user with
  no compliance tag and `user_role != 'admin'`, confirm only rows where
  `user_email` matches their own login email are visible.
- **Legacy compliance users still work:** pick a real (not throwaway) user
  whose `profiles.role` was `compliance` before this rollout — confirm they
  still see all governance events. This is the direct proof that Step 1's
  `0014` tag-seeding landed before this cutover; if it hadn't, this user
  would have just lost broad-read access.

## 6. Roll out to jnow

Repeat Steps 2 through 5 on the `jnow` box, using `'jnow'` in the Step 2 seed
SQL and verifying against `https://ollie.jnow.io` / `ollie.jnow.io`'s
orchestrator. Step 1 (`0013`+`0014`) does not need to be repeated — same
shared Supabase project, already applied. Only proceed once every sandbox
check above has passed.

## 7. Rollback (per stage, independently reversible)

Each stage below is independently revertible — you do not need to unwind
later stages to roll back an earlier one, and vice versa (rolling back
`0016` does not require reverting `0015`, since `0015` is additive and
harmless on its own).

**`0016` (governance RLS):** re-apply the pre-2a.3 policy from
`0005_governance_events.sql`:

```sql
drop policy if exists governance_events_select on public.governance_events;

create policy governance_events_select on public.governance_events
  for select to authenticated
  using (
    coalesce(auth.jwt() ->> 'user_role', '') in ('broker', 'compliance')
    or user_email = coalesce(auth.jwt() ->> 'email', '')
  );
```

**`0015` (hook):** re-apply `0001`'s original hook body (see the exact SQL in
Step 3 above under the LOGIN GATE). Verify login still works immediately
after reverting, same as when rolling forward.

**Orchestrator + frontend (per box):**

```bash
docker tag justnorthow/ollie-hermes-frontend:rollback-pre-identity \
           justnorthow/ollie-hermes-frontend:latest
docker compose up -d
```

```bash
cd ~/ollie-hermes-orchestrator
git checkout <pre-rollout-sha>
systemctl --user restart ollie-orchestrator
```

**`0013`/`0014` (tables/tags) and the Step 2 tier seed:** leave in place — all
additive and inert without the code reading them. `user_tags` rows are safe
to leave even after rolling back `0015`/`0016`; they simply stop being read
by anything until those stages are re-applied. No data cleanup required.
