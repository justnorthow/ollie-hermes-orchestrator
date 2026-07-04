# Roll out instance-scoped governance visibility (Phase 2b)

Ships instance-scoped `governance_events` visibility: a brokerage owner
(`account_admin`+) sees their own brokerage's governance/compliance audit
trail, JNOW compliance staff (the global `compliance` tag) still sees every
brokerage, and a brokerage's manager gets that same brokerage-scoped view
only when an admin opts them in via a new per-user `governance_view` flag.
Ships three things together: migration `0017` (additive `instance_id` +
`governance_view` columns, plus the RLS policy swap), the orchestrator
(`instance_id` stamping on every governance write, `whoami.governanceView`,
and `PUT /v1/admin/users/{id}/governance-view`), and the frontend (Compliance
/ Verification / TRAIGA Report nav+route gating on `governanceView`). Design:
`docs/superpowers/specs/2026-07-04-governance-tenancy-phase2b-design.md`.

Roll out to the **sandbox box first** (`ollie@178.105.216.167`,
`olliesandbox.jnow.io`). Only move to `jnow` (`ollie.jnow.io`) after every
sandbox check below passes.

**Read this before starting — the ordering is load-bearing, not just numeric
sequence:**

`governance_events` (and its RLS) is a **single table shared** across the
sandbox and jnow boxes — every box's orchestrator writes to it via the
service role, and there is one Supabase project (`kpdqhntsvjzhqjeupzsj`)
behind both. That means applying `0017` affects BOTH boxes at once, and the
three deploy steps below MUST happen in this order, on a given box, for the
cutover to be safe:

1. **Deploy the orchestrator FIRST** (instance_id stamping + `whoami`/admin
   verb). This makes new `governance_events` rows start carrying
   `instance_id` *while the OLD `0016` policy is still active* — `0016` has
   no idea the column exists, so it just ignores it. New rows accumulate
   `instance_id` safely with zero visibility change, because the policy
   deciding who can read them hasn't switched yet.
2. **Apply migration `0017`** (the two additive columns + the RLS policy
   swap). Only now do reads actually use `instance_id` — and by this point
   every *new* event already has it, because Step 1 landed first. If you
   applied `0017` before the orchestrator, the RLS would already be
   instance-scoped while rows kept landing with `instance_id = null`,
   which would fall through only to the compliance-tag/own-email clauses of
   the new policy for that box's brokers — the entire reason it must come
   second.
3. **Deploy the frontend** (`governanceView` nav/route gate on Compliance /
   Verification / TRAIGA Report). This can only meaningfully gate on a real
   `governanceView` value once `whoami` (Step 1) and the RLS (Step 2) are
   both live — deploying it earlier just means the flag is always `false`
   from an unfinished backend, hiding nav items that should already be
   visible for account_admins.

**Do NOT reorder these.** Each step's safety depends on the previous one
having already landed on that specific box.

**Shared-table caveat during the jnow gap.** Because `0017` is one migration
against one shared table, applying it while rolling out sandbox-first means
it takes effect for jnow too, immediately — even though jnow's orchestrator
hasn't deployed yet. During that window, jnow's *new* governance events are
still being written by jnow's **old** orchestrator, so they lack
`instance_id` (null). Under the new instance-scoped policy, a null
`instance_id` never matches the per-instance `user_roles` join (clause b),
so jnow's brokers can only see governance events via the global
`compliance` tag or their own-email fallback until jnow's own orchestrator
deploys and starts stamping `instance_id`. This is a **temporary reduction
in visibility for jnow, not a leak** — no jnow broker gains access to
another instance's data, and no other instance gains access to jnow's data.
Keep this window short: finish jnow's own orchestrator → `0017` (already
applied, no-op to re-run) → frontend sequence promptly after sandbox
verification passes.

## 0. Preconditions

- **Phase 2 is live on the target box.** This rollout assumes the Phase 2
  (RBAC tiers, `user_roles`, `INSTANCE_ID`) and Phase 2a.3 (global tags,
  `0016`) rollouts already completed on the box you're deploying to — see
  `docs/runbooks/rbac-phase2a-rollout.md` and
  `docs/runbooks/identity-consolidation-2a3-rollout.md`.
- **`INSTANCE_ID` is set on the box.** Confirm the orchestrator's env file
  (`~/.config/ollie-orchestrator/.env`) already has `INSTANCE_ID=sandbox` on
  the sandbox box (`ollie@178.105.216.167`) — this was set during Phase 2 and
  should not need to change here, but verify it before proceeding; every
  `instance_id` stamped in this rollout comes directly from this value.
- **jnow pre-flight (do this before touching the jnow box, well ahead of the
  jnow deploy step):** on the jnow box, `git pull` the install repo to
  commit `8f37bdf`, then verify `~/hermes-stack/.env` has `SUPABASE_URL`,
  `SUPABASE_ANON_KEY`, and `SUPABASE_COOKIE_DOMAIN` all populated —
  **before** any frontend container recreate. These three vars were
  observed being blanked out on re-provision; if a frontend recreate
  happens while they're blank, login breaks on that box. Check them with:
  ```bash
  grep -E '^(SUPABASE_URL|SUPABASE_ANON_KEY|SUPABASE_COOKIE_DOMAIN)=' ~/hermes-stack/.env
  ```
  All three must show a non-empty value. If any is blank, fix `.env` first
  and confirm login still works before recreating any frontend container as
  part of this rollout.

## 1. Sandbox — deploy the orchestrator

On the sandbox box (`ollie@178.105.216.167`):

```bash
cd ~/ollie-hermes-orchestrator
git pull origin master
systemctl --user restart ollie-orchestrator
```

Access the box from Windows PowerShell using:

```powershell
ssh -F NUL ollie@178.105.216.167
```

`-F NUL` bypasses the machine's `~/.ssh/config`, which otherwise hijacks
this connection with a `revomate` key — without it you'll auth as the wrong
identity or get rejected. For any multi-line remote script, base64-encode it
locally and pipe it through, e.g.:

```powershell
$script = @'
cd ~/ollie-hermes-orchestrator
git pull origin master
systemctl --user restart ollie-orchestrator
'@
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($script))
ssh -F NUL ollie@178.105.216.167 "echo $b64 | base64 -d | bash"
```

The 1Password SSH agent will prompt for approval against the "Ollie sandbox"
key — **John must be present** to approve it; this is not something that can
be scripted around or run unattended.

Verify the deploy landed and is stamping `instance_id` — check that a fresh
guardrail or attestation event now carries the column (either trigger one
live, e.g. by sending a blocked-content test prompt through a governed
agent, or inspect the most recent `governance_events` row for the box in the
Supabase table editor):

```sql
select id, app, event_type, instance_id, created_at
from public.governance_events
order by created_at desc
limit 5;
```

Expect the newest row(s) from this box to show `instance_id = 'sandbox'`.
Do NOT proceed to Step 2 until you've confirmed at least one fresh row
carries it — that's the proof the old `0016` policy is now accumulating
`instance_id` safely, which is the entire premise of doing this step first.

## 2. Sandbox — apply migration `0017`

Migration file: `development/core/supabase/migrations/0017_governance_tenancy.sql`
in the jnow-site repo. Shared Supabase project ref: `kpdqhntsvjzhqjeupzsj`
(same project for both boxes — this only needs to be applied once, total,
not once per box).

1. Open the Supabase dashboard for that project → **SQL Editor**.
2. Paste the full contents of `0017_governance_tenancy.sql` and run it —
   same process as prior `development/core` migrations.
3. Confirm in **Table Editor**:
   - `public.governance_events` has a new nullable `instance_id text` column,
     and an index `governance_events_instance_idx` on it.
   - `public.user_roles` has a new `governance_view boolean not null default
     false` column.
   - The `governance_events_select` policy has been replaced — open the
     policy definition and confirm it matches the §4 form (compliance tag OR
     the per-instance `user_roles` join on tier/`governance_view` OR
     own-email), not the old `0016` `user_role = 'admin'` form.

Remember: because this is one shared project, this single apply affects
BOTH the sandbox and jnow boxes' RLS simultaneously — see the shared-table
caveat above. Proceed to Step 3 (sandbox frontend) promptly, and keep the
jnow-side gap (Steps 1-3 not yet done on jnow) as short as practical.

## 3. Sandbox — deploy the frontend

Tag off the current image before touching it, following the same
`:rollback-pre-<feature>` convention used for prior rollouts, using a **new**
tag so you never clobber a tag prod may still be sharing:

```bash
docker tag justnorthow/ollie-hermes-frontend:latest \
           justnorthow/ollie-hermes-frontend:rollback-pre-governance-2b
```

Rebuild and push `justnorthow/ollie-hermes-frontend:latest`, tagging this
rollout's build `:governance-2b`, from the frontend repo (only the
Compliance/Verification/TRAIGA nav+route gate changes — no Compliance page
content changes in this phase). On the box:

```bash
docker compose pull
docker compose up -d
```

## 4. Smoke tests (sandbox)

All on-box against `http://127.0.0.1:9123` (same convention as prior
rollouts — hand-set `X-Auth-User-Id` headers only work against the
orchestrator directly; the public edge overwrites them with the logged-in
user's real identity), plus a pass through the UI at
`https://olliesandbox.jnow.io`.

You'll need: John's `account_admin`/`platform_operator` UUID
(`1a2b341c-0d01-418f-9fdb-4cebc27058c7`), a throwaway plain-`member` user's
UUID, and a second throwaway `member` user to flag as a manager via the new
admin verb — create these for this test, do not reuse production users.

- **`whoami.governanceView` — account_admin true, plain member false:**
  ```bash
  curl -s -H "Authorization: Bearer $ORCHESTRATOR_KEY" -H "X-Auth-User-Id: 1a2b341c-0d01-418f-9fdb-4cebc27058c7" \
       http://127.0.0.1:9123/v1/whoami
  ```
  Expect `200` with `"governanceView": true`.
  ```bash
  curl -s -H "Authorization: Bearer $ORCHESTRATOR_KEY" -H "X-Auth-User-Id: <throwaway-member-uuid>" \
       http://127.0.0.1:9123/v1/whoami
  ```
  Expect `200` with `"governanceView": false`.

- **`PUT /v1/admin/users/{id}/governance-view` — account_admin allowed,
  member forbidden, and it takes effect:**
  ```bash
  curl -s -o /dev/null -w "%{http_code}\n" \
       -H "Authorization: Bearer $ORCHESTRATOR_KEY" -H "X-Auth-User-Id: 1a2b341c-0d01-418f-9fdb-4cebc27058c7" \
       -H "Content-Type: application/json" -d '{"enabled":true}' \
       -X PUT http://127.0.0.1:9123/v1/admin/users/<throwaway-manager-uuid>/governance-view
  ```
  Expect `200` with body `{"userId": "<throwaway-manager-uuid>", "governanceView": true}`.
  ```bash
  curl -s -o /dev/null -w "%{http_code}\n" \
       -H "Authorization: Bearer $ORCHESTRATOR_KEY" -H "X-Auth-User-Id: <throwaway-member-uuid>" \
       -H "Content-Type: application/json" -d '{"enabled":true}' \
       -X PUT http://127.0.0.1:9123/v1/admin/users/<throwaway-manager-uuid>/governance-view
  ```
  Expect `403`.
  ```bash
  curl -s -H "Authorization: Bearer $ORCHESTRATOR_KEY" -H "X-Auth-User-Id: <throwaway-manager-uuid>" \
       http://127.0.0.1:9123/v1/whoami
  ```
  Expect `200` with `"governanceView": true` for the now-flagged manager (the
  30s role-cache TTL from prior phases applies here too — if you still see
  `false` immediately after the PUT, wait for the cache to expire and
  re-check before treating it as a failure).

- **Compliance page visibility, per role:**
  - Log in as John (`account_admin`) at `https://olliesandbox.jnow.io` — the
    Compliance nav item is visible, and the page shows this instance's
    governance events.
  - Log in as the now-flagged manager (`governance_view = true`) — the
    Compliance nav item is visible, and the page shows the same
    instance-scoped events (RLS does the filtering; the page has no logic
    change from prior phases).
  - Log in as a plain member (no tier escalation, no `governance_view`
    flag) — the Compliance/Verification/TRAIGA Report nav items are absent.

- **Optional non-prod RLS spot-check.** `0017`'s effect can also be verified
  directly at the SQL level, independent of the orchestrator/frontend, by
  running `development/core/supabase/tests/rls_0017_governance_tenancy.sql`
  (jnow-site repo) against a **local/scratch Supabase instance only — never
  prod**. It seeds its own fixtures (an account_admin, a flagged manager, an
  unflagged member, a compliance-tagged user, and a historical
  `instance_id = null` row), asserts all four §8 visibility behaviors via
  `raise exception` on any mismatch, and wraps everything in
  `begin … rollback` so it leaves no residue. Expect five
  `NOTICE: PASS …` lines and no `FAIL`/exception.

Only proceed to the jnow box once every check above passes on sandbox.

## 5. Roll out to jnow

Confirm the jnow pre-flight (§0) is done and login on jnow already works
before doing anything else. Then repeat Steps 1, 3, and 4 on the `jnow` box
(`ollie.jnow.io`), using `https://ollie.jnow.io` in place of
`https://olliesandbox.jnow.io` and jnow's own throwaway test users. Step 2
(`0017`) does **not** need to be re-applied — it's the same shared Supabase
project, already applied when sandbox rolled it out; just confirm (Table
Editor) that jnow's own fresh governance events are stamped with
`instance_id = 'jnow'` once jnow's orchestrator (Step 1 equivalent) has
deployed, closing the shared-table visibility gap described above.

## 6. Rollback

Each piece below is independently revertible; do all of them on any box
that received this rollout.

**RLS (`0017` → pre-2b):** re-apply `0016`'s exact
`governance_events_select` policy in the Supabase SQL editor for the shared
project:

```sql
drop policy if exists governance_events_select on public.governance_events;
create policy governance_events_select on public.governance_events
  for select to authenticated
  using (
    (auth.jwt() -> 'tags') ? 'compliance'
    or coalesce(auth.jwt() ->> 'user_role', '') = 'admin'
    or user_email = coalesce(auth.jwt() ->> 'email', '')
  );
```

**Orchestrator (per box):**

```bash
cd ~/ollie-hermes-orchestrator
git checkout <pre-2b-sha>
systemctl --user restart ollie-orchestrator
```

**Frontend (per box):**

```bash
docker tag justnorthow/ollie-hermes-frontend:rollback-pre-governance-2b \
           justnorthow/ollie-hermes-frontend:latest
docker compose up -d
```

**Columns:** the `instance_id` column on `governance_events` and the
`governance_view` column on `user_roles` are additive and inert once the
RLS policy and orchestrator/frontend code that read them are reverted — they
may stay in place. No data cleanup required.
