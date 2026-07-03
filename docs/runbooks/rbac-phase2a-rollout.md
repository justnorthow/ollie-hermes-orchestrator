# Roll out RBAC tiers + agent-scope enforcement (Phase 2a)

Ships four things together: a per-instance `user_roles`/`role_labels` table in
Supabase, a `member`/`manager`/`account_admin`/`platform_operator` tier
resolved per user (`GET /v1/whoami`), fail-closed enforcement of that tier
against each agent's `company`/`user` scope on every run and session
endpoint, and an admin API (`GET /v1/admin/users`,
`PUT /v1/admin/users/{user_id}/role`, `GET`/`PUT /v1/admin/role-labels`) to
manage roles and per-instance display labels. Design:
`docs/superpowers/specs/2026-07-03-rbac-scope-taxonomy-phase2a-design.md`.

Roll out to the **sandbox box first** (`ollie@178.105.216.167`,
`olliesandbox.jnow.io`). Only move to `jnow` (`ollie.jnow.io`) after every
sandbox smoke test in Step 7 passes.

**Read this before starting:** the `user_roles` table starts empty on every
box. Until John's own row is seeded, `resolve_tier` fail-closes everyone —
including John — to `member`, which means everyone loses access to every
`company`-scoped agent the moment enforcement goes live. Step 4 (seed) MUST
happen before Step 5 (restart + verify), or you lock yourself out of every
agent except the user-scoped Ollie. There is no other way in at that point
except editing the row directly in the Supabase table editor.

## 1. Apply the migration

Migration file: `development/core/supabase/migrations/0012_user_roles.sql`
in the jnow-workspace repo.

Shared Supabase project ref: `kpdqhntsvjzhqjeupzsj`.

1. Open the Supabase dashboard for that project → **SQL Editor**.
2. Paste the full contents of `0012_user_roles.sql` and run it — same
   process as prior `development/core` migrations (e.g.
   `0011_agent_sessions.sql`).
3. Confirm: **Table Editor** → `public.user_roles` exists (columns
   `instance_id`, `user_id`, `tier`, `assigned_by`, `created_at`,
   `updated_at`; primary key `(instance_id, user_id)`; RLS enabled with a
   `user_roles_select_own` policy) and `public.role_labels` exists (columns
   `instance_id`, `tier`, `label`; primary key `(instance_id, tier)`; RLS
   enabled with a `role_labels_select_all` policy). Both tables have no
   write policies — only the service-role key (used by the orchestrator's
   admin API) can insert or update rows.

This only needs to happen once — it's the same shared project for both
boxes.

## 2. Set `INSTANCE_ID`

Every role row is scoped by `instance_id`, so each box must identify itself
before any role lookup means anything. Per box, in the orchestrator's env
file (`~/.config/ollie-orchestrator/.env`, the same file that already holds
`ORCHESTRATOR_KEY`, `HERMES_DASHBOARD_URLS`, etc.):

```bash
# Sandbox box:
INSTANCE_ID=sandbox

# jnow (prod) box:
INSTANCE_ID=jnow
```

Note the value used on each box — it's the exact string you'll match in the
Step 4 seed SQL and every `user_roles`/`role_labels` row for that box.

## 3. Mark Ollie user-scoped

In `~/hermes-stack/.env`, the `AGENTS_JSON` map, add `"scope":"user"` to the
`default` (Ollie) entry so every authenticated user — regardless of tier —
keeps access to their own assistant. Leave every other agent to its default
`"scope":"company"` (company-wide, gated by tier), and add
`"manager_visible":true` to any company agent a `manager`-tier user should
also be able to reach (in addition to `account_admin`/`platform_operator`,
who can already reach everything).

Example fragment:

```json
{
  "default": {"scope": "user", "...": "..."},
  "olivia-marketing": {"scope": "company", "manager_visible": true, "...": "..."},
  "some-other-company-agent": {"scope": "company", "...": "..."}
}
```

No frontend rebuild is required for this step — the SPA's agent picker reads
`reachableAgentIds` from `whoami` (Step 6), not from a locally bundled agent
list. A box-side `AGENTS_JSON` edit plus an orchestrator restart (Step 5) is
enough for enforcement to take effect.

## 4. Seed roles (fail-closed bootstrap — do this before Step 5)

The table is empty on a fresh apply, so everyone (including you) resolves to
`member` and would lose access to every company-scoped agent the instant
enforcement goes live. Seed your own `platform_operator` row FIRST, before
restarting the orchestrator or relying on the admin API to do it for you —
the admin API itself requires `account_admin+` to call, which you won't have
until this row exists.

In the Supabase SQL editor, for the **sandbox** box:

```sql
insert into public.user_roles (instance_id, user_id, tier)
values ('sandbox', '1a2b341c-0d01-418f-9fdb-4cebc27058c7', 'platform_operator')
on conflict (instance_id, user_id) do update set tier = excluded.tier;
```

`1a2b341c-0d01-418f-9fdb-4cebc27058c7` is John's Supabase user UUID. Repeat
with `'jnow'` in place of `'sandbox'` when you get to the jnow box in Step 8.

## 5. Restart the orchestrator and verify whoami

```bash
systemctl --user restart ollie-orchestrator
```

```bash
curl -s -H "Authorization: Bearer $ORCHESTRATOR_KEY" -H "X-Auth-User-Id: 1a2b341c-0d01-418f-9fdb-4cebc27058c7" \
     http://127.0.0.1:9123/v1/whoami
```

Expect a `200` with your tier (`platform_operator`) and a full
`reachableAgentIds` list (every configured agent, since `platform_operator`
can reach everything). If you instead see `tier: "member"` and only
`["default"]`, the seed in Step 4 didn't land for this box's `INSTANCE_ID` —
stop here and fix the seed before proceeding; do not continue to Step 6 in
that state.

## 6. Deploy the frontend

Tag off the current image before touching it, following the same
`:rollback-pre-<feature>` convention used for the Phase 1 rollout — use a
**new** tag, not the sandbox's prior `:sessions-phase01` tag, so you never
clobber a tag prod may still be sharing:

```bash
docker tag justnorthow/ollie-hermes-frontend:latest \
           justnorthow/ollie-hermes-frontend:rollback-pre-rbac
```

Rebuild and push `justnorthow/ollie-hermes-frontend:latest`, tagging this
rollout's build `:rbac-phase2a`, from the frontend repo (no nginx changes
needed for this phase — the picker change is purely in the SPA's use of
`whoami`'s `reachableAgentIds`).

On the box:

```bash
docker compose pull
docker compose up -d
```

## 7. Smoke tests

All on-box against `http://127.0.0.1:9123` (mirroring how Phase 1's
cross-user checks were run — hand-set `X-Auth-User-Id` headers only work
against the orchestrator directly; the public edge's `auth_request` overwrites
them with the logged-in user's real identity), plus a pass through the UI at
`https://olliesandbox.jnow.io`.

You'll need a second, throwaway Supabase user's UUID for the "member" checks
below — create one for this test, do not reuse John's.

- **whoami, both identities:**
  ```bash
  curl -s -H "Authorization: Bearer $ORCHESTRATOR_KEY" -H "X-Auth-User-Id: 1a2b341c-0d01-418f-9fdb-4cebc27058c7" \
       http://127.0.0.1:9123/v1/whoami
  ```
  Expect tier `platform_operator`, full `reachableAgentIds`.
  ```bash
  curl -s -H "Authorization: Bearer $ORCHESTRATOR_KEY" -H "X-Auth-User-Id: <throwaway-user-uuid>" \
       http://127.0.0.1:9123/v1/whoami
  ```
  Expect tier `member` (no row for this user — fail-closed default),
  `reachableAgentIds` = `["default"]` only.

- **Member blocked from a company agent's run:**
  ```bash
  curl -s -o /dev/null -w "%{http_code}\n" \
       -H "Authorization: Bearer $ORCHESTRATOR_KEY" -H "X-Auth-User-Id: <throwaway-user-uuid>" \
       -H "Content-Type: application/json" -d '{"input":"hi"}' \
       http://127.0.0.1:9123/v1/runs/olivia-marketing
  ```
  Expect `403` with body `{"detail":"Forbidden"}`.

- **Member allowed on the user-scoped Ollie agent:**
  ```bash
  curl -s -o /dev/null -w "%{http_code}\n" \
       -H "Authorization: Bearer $ORCHESTRATOR_KEY" -H "X-Auth-User-Id: <throwaway-user-uuid>" \
       -H "Content-Type: application/json" -d '{"input":"hi"}' \
       http://127.0.0.1:9123/v1/runs/default
  ```
  Expect not-403 (normal run-creation response).

- **Member blocked from a company agent's sessions:**
  ```bash
  curl -s -o /dev/null -w "%{http_code}\n" \
       -H "Authorization: Bearer $ORCHESTRATOR_KEY" -H "X-Auth-User-Id: <throwaway-user-uuid>" \
       http://127.0.0.1:9123/v1/sessions/olivia-marketing
  ```
  Expect `403` with body `{"detail":"Forbidden"}`.

- **Member blocked from the admin API; John allowed:**
  ```bash
  curl -s -o /dev/null -w "%{http_code}\n" \
       -H "Authorization: Bearer $ORCHESTRATOR_KEY" -H "X-Auth-User-Id: <throwaway-user-uuid>" \
       http://127.0.0.1:9123/v1/admin/users
  ```
  Expect `403`.
  ```bash
  curl -s -o /dev/null -w "%{http_code}\n" \
       -H "Authorization: Bearer $ORCHESTRATOR_KEY" -H "X-Auth-User-Id: 1a2b341c-0d01-418f-9fdb-4cebc27058c7" \
       http://127.0.0.1:9123/v1/admin/users
  ```
  Expect `200`.

- **Role change via admin API takes effect:**
  ```bash
  curl -s -o /dev/null -w "%{http_code}\n" \
       -H "Authorization: Bearer $ORCHESTRATOR_KEY" -H "X-Auth-User-Id: 1a2b341c-0d01-418f-9fdb-4cebc27058c7" \
       -H "Content-Type: application/json" -d '{"tier":"manager"}' \
       http://127.0.0.1:9123/v1/admin/users/<throwaway-user-uuid>/role
  ```
  Expect `200`, plus a new row in `governance_events` for this write
  (check the Supabase table editor or query it). Re-run the member's
  `whoami` — within the 30s role-cache TTL you may still see the old tier;
  after it expires, `reachableAgentIds` now includes any `company`-scoped
  agent marked `manager_visible: true`, in addition to `default`.

- **Role/label admin endpoints round-trip:**
  ```bash
  curl -s -H "Authorization: Bearer $ORCHESTRATOR_KEY" -H "X-Auth-User-Id: 1a2b341c-0d01-418f-9fdb-4cebc27058c7" \
       http://127.0.0.1:9123/v1/admin/role-labels
  ```
  Expect `200` with the default label set for all four tiers
  (`member`/`manager`/`account_admin`/`platform_operator`).

- **Phase 1 still holds (ownership check is independent of RBAC):** an
  allowed agent with a foreign (someone else's) session id still returns
  `403` not-found — RBAC gates *which agents* you can reach; the Phase 1
  ownership check still gates *which sessions within an agent* you can
  reach, and RBAC passing doesn't bypass it.

- **In the UI:** log in as the throwaway member — the agent picker shows
  only Ollie. Log in as John — the picker shows every configured agent.

Only proceed to the `jnow` box once every check above passes on sandbox.

## 8. Roll out to jnow

Repeat Steps 2, 4, 5, 6 and 7 on the `jnow` box, using `INSTANCE_ID=jnow` and
the `'jnow'` instance id in the Step 4 seed SQL. Step 1 (the migration) and
Step 3 (`AGENTS_JSON` scope — assuming both boxes share the same agent
config conventions) do not need to be repeated if already applied/shared;
verify each box's own `~/hermes-stack/.env` independently regardless, since
`AGENTS_JSON` is box-local.

## 9. Rollback

On each box that was rolled out, do BOTH of the following:

Frontend image (per box):

```bash
docker tag justnorthow/ollie-hermes-frontend:rollback-pre-rbac \
           justnorthow/ollie-hermes-frontend:latest
docker compose up -d
```

Orchestrator (per box — use the pre-rollout SHA recorded for this box before
starting):

```bash
cd ~/ollie-hermes-orchestrator
git checkout <pre-rollout-sha>
systemctl --user restart ollie-orchestrator
```

The `user_roles`/`role_labels` migration is additive and safe to leave in
place — no migration rollback needed. The role rows themselves are inert
without the resolving/enforcement code running, so they can stay too; no
data cleanup required.
