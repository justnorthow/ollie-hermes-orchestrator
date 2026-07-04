# Governance Tenancy Phase 2b — Instance-Scoped Governance Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make governance-event visibility instance-scoped so a brokerage owner sees only their brokerage's governance events, JNOW compliance staff see all brokerages, and a manager's view is configurable per-manager.

**Architecture:** `governance_events` is a single shared, instance-blind Supabase table written by every box's orchestrator. Phase 2b adds an `instance_id` column that the orchestrator stamps from its `INSTANCE_ID` on every write, plus a per-user `governance_view` flag on `user_roles`, then rewrites the RLS `SELECT` policy to grant broad read via (a) the global `compliance` tag, (b) an `account_admin`+ tier OR `governance_view` grant on `user_roles` for *that row's* `instance_id`, or (c) own-email. The orchestrator's `whoami` exposes a `governanceView` bool and a new admin verb toggles the flag; the frontend Compliance nav/routes gate on it.

**Tech Stack:** Supabase Postgres (SQL migrations + RLS), Python FastAPI orchestrator (pytest), React + TypeScript frontend (Vitest).

## Global Constraints

- **Additive & reversible only.** Every schema change is `add column if not exists` / `default false`; no backfill; historical rows keep `instance_id = null`. The RLS change is a policy swap (rollback = re-apply `0016`'s policy).
- **Shared table.** `governance_events` and its RLS are shared across the sandbox and jnow boxes (one Supabase project `kpdqhntsvjzhqjeupzsj`). Applying `0017` affects both boxes at once. `instance_id` only starts populating after each box's orchestrator deploys.
- **Staged rollout ordering (do NOT reorder):** deploy orchestrator (instance_id stamping) → apply `0017` → deploy frontend. Sandbox-first, then jnow. (Build order in this plan is independent; ordering matters only at deploy time — captured in Task 5's runbook.)
- **Branch:** `governance-2b` in each repo (`ollie-hermes-orchestrator`, `ollie-hermes-frontend`, and the jnow-site worktree holding jnow-workspace `main`). Never `git switch main` inside `D:\workspaces\jnow-workspace`; the migrations live in the `D:\workspaces\jnow-site` worktree.
- **Commit trailer (every commit):** `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- **Orchestrator test command:** `python -m pytest tests/ src/ -q` — a baseline of **17 pre-existing Windows failures / 308 passed** is accepted; a task is green if it adds no NEW failures.
- **Frontend test command:** `npx vitest run` from **Git Bash, not PowerShell** (some tests shell out to `sh`), plus `npx tsc --noEmit`.
- **Tier canon (unchanged):** `member < manager < account_admin < platform_operator`. "account_admin+" means `account_admin` or `platform_operator`.
- **Governance visibility rides tier/grant, NOT the JWT `user_role` claim.** This phase stops *reading* `user_role` for governance (retires the legacy `broker`/`admin` coupling); `user_role` emission stays as-is for now.

---

## File Structure

| File | Repo | Responsibility |
|---|---|---|
| `.../supabase/migrations/0017_governance_tenancy.sql` | jnow-site | Additive `instance_id` + `governance_view` columns + RLS policy swap |
| `.../supabase/tests/rls_0017_governance_tenancy.sql` | jnow-site | Self-cleaning (begin/rollback) SQL-level RLS verification |
| `src/api/runs.py` | orchestrator | Stamp `instance_id` on guardrail + attestation writes (`_instance_id` helper, `_emit_guardrail`, `gen()`) |
| `src/api/admin.py` | orchestrator | Stamp `instance_id` on admin audit writes; add `governanceView` to `whoami`; add `PUT …/governance-view` verb + `GovernanceViewBody` |
| `src/api/roles.py` | orchestrator | `resolve_governance_view()` + `_fetch_gov()` + `_gov_cache`; `set_governance_view()`; extend `invalidate_cache` to sweep the gov cache |
| `src/api/test_runs_guardrail.py` | orchestrator | Assert every write carries `instance_id` |
| `tests/test_roles.py` | orchestrator | Cover `resolve_governance_view` cache + `set_governance_view` |
| `tests/test_admin_api.py` | orchestrator | Cover `whoami.governanceView`, the new verb, and admin-event `instance_id` |
| `src/hooks/useIdentity.ts` | frontend | Add `governanceView: boolean` to `Identity` + fetch + fail-safe |
| `src/adapters/orchestrator/OrchestratorTypes.ts` | frontend | Add `governanceView?: boolean` to `WhoamiResponse` (type honesty) |
| `src/components/RoleRoute.tsx` | frontend | `governanceView?` axis in the OR gate |
| `src/components/Layout.tsx` | frontend | `governanceView?` on `NavItemDef`; set on the 3 compliance items; OR in `NavItemRow` |
| `src/App.tsx` | frontend | Pass `governanceView` on the 3 compliance routes |
| `src/hooks/__tests__/useIdentity.test.ts`, `src/components/RoleRoute.test.tsx`, `src/components/Layout*.test.tsx` | frontend | New assertions + add `governanceView` to every `useIdentity` mock |
| `docs/runbooks/governance-tenancy-2b-rollout.md` | orchestrator | Staged rollout + smoke tests + rollback |

---

## Task 1: Migration `0017` — instance_id + governance_view columns + RLS swap

**Files:**
- Create: `D:\workspaces\jnow-site\development\core\supabase\migrations\0017_governance_tenancy.sql`
- Create (verification, NOT applied in sequence): `D:\workspaces\jnow-site\development\core\supabase\tests\rls_0017_governance_tenancy.sql`

**Interfaces:**
- Produces: column `public.governance_events.instance_id text` (nullable); column `public.user_roles.governance_view boolean not null default false`; policy `governance_events_select` rewritten to the §4 form. Task 2 writes `instance_id`; Task 3 reads `governance_view`.
- Consumes: existing `public.user_roles(instance_id, user_id, tier)` + its `user_roles_select_own` RLS (from `0012`); existing `public.governance_events(user_email, …)` (from `0005`); the `0016` policy this replaces.

**Context — the exact policy being replaced (`0016_governance_rls_tags.sql`):**

```sql
create policy governance_events_select on public.governance_events
  for select to authenticated
  using (
    (auth.jwt() -> 'tags') ? 'compliance'
    or coalesce(auth.jwt() ->> 'user_role', '') = 'admin'
    or user_email = coalesce(auth.jwt() ->> 'email', '')
  );
```

- [ ] **Step 1: Write the migration file**

Create `0017_governance_tenancy.sql` with exactly this content:

```sql
-- 0017_governance_tenancy.sql — instance-scoped governance visibility (Phase 2b).
-- Spec: ollie-hermes-orchestrator docs/superpowers/specs/2026-07-04-governance-tenancy-phase2b-design.md
--
-- governance_events is a SINGLE shared, instance-blind table (every box's
-- orchestrator writes it via the service role). This migration scopes broad-read
-- by instance so a brokerage owner sees only their own brokerage's events, JNOW
-- compliance (global `compliance` tag) sees all, and a per-user `governance_view`
-- grant opts a specific manager in. Additive columns (no backfill); the policy
-- swap REPLACES 0016. Historical rows keep instance_id = null (compliance/own-email
-- only). STAGED: apply ONLY after the orchestrator that stamps instance_id is
-- deployed (see docs/runbooks/governance-tenancy-2b-rollout.md).

-- 1. instance tag on each governance event (nullable; historical rows stay null).
alter table public.governance_events add column if not exists instance_id text;
create index if not exists governance_events_instance_idx
  on public.governance_events (instance_id);

-- 2. per-user, per-instance governance-view grant (the configurable manager;
--    owners are covered by tier and never need this set).
alter table public.user_roles
  add column if not exists governance_view boolean not null default false;

-- 3. Replace the 0016 broad-read policy with an instance-scoped one.
drop policy if exists governance_events_select on public.governance_events;

create policy governance_events_select on public.governance_events
  for select to authenticated
  using (
    -- (a) JNOW cross-brokerage oversight: the global compliance tag.
    (auth.jwt() -> 'tags') ? 'compliance'
    -- (b) Your own brokerage: account_admin+ tier OR an explicit per-user
    --     governance grant for THIS row's instance. Tier is never a JWT claim
    --     (Phase 2a.3), so we read user_roles directly; the subquery is filtered
    --     to ur.user_id = auth.uid(), which user_roles' select-own RLS allows.
    or exists (
      select 1 from public.user_roles ur
      where ur.user_id = auth.uid()
        and ur.instance_id = governance_events.instance_id
        and (ur.tier in ('account_admin', 'platform_operator') or ur.governance_view)
    )
    -- (c) Personal fallback: your own rows.
    or user_email = coalesce(auth.jwt() ->> 'email', '')
  );

comment on column public.governance_events.instance_id is
  'Phase 2b: the box/brokerage instance that produced this event (orchestrator INSTANCE_ID); null for pre-feature rows.';
comment on column public.user_roles.governance_view is
  'Phase 2b: per-user, per-instance opt-in to the brokerage governance audit view (the configurable manager); owners are covered by tier.';
```

- [ ] **Step 2: Write the RLS verification script**

Create `.../supabase/tests/rls_0017_governance_tenancy.sql`. This is a **manual, self-cleaning** script (wrapped in `begin … rollback`) — it is NOT part of the numbered migration sequence and must never be applied to production as a migration. Run it in a local `supabase` instance (preferred) or a scratch Supabase project's SQL editor; it rolls its own seed data back. It asserts the four §8 behaviors and `raise exception`s (aborting the transaction) on any mismatch.

```sql
-- rls_0017_governance_tenancy.sql — MANUAL RLS verification for 0017. NOT a migration.
-- Assumes 0017 is already applied. Seeds fixtures, checks visibility as several
-- simulated users, then ROLLS BACK (non-destructive). Prefer a local `supabase`
-- instance; safe to run in a scratch project's SQL editor. DO NOT rename to 00NN_*.
begin;

-- Fixture instances/users. UUIDs are fixed test values.
--   admin_x  = account_admin of instance X
--   mgr_x    = member of X, governance_view = true  (configurable manager, opted in)
--   plain_x  = member of X, governance_view = false (no grant)
insert into public.user_roles (instance_id, user_id, tier, governance_view) values
  ('X', '00000000-0000-0000-0000-0000000000a1', 'account_admin', false),
  ('X', '00000000-0000-0000-0000-0000000000a2', 'member',        true),
  ('X', '00000000-0000-0000-0000-0000000000a3', 'member',        false);

-- Events: 2 in X, 1 in Y, 1 historical (instance_id null, owned by plain_x's email).
insert into public.governance_events
  (user_email, user_role, app, event_type, status, instance_id) values
  ('someone@x', 'agent', 'real-estate', 'guardrail.blocked', 'block', 'X'),
  ('other@x',   'agent', 'real-estate', 'attestation.pass',  'pass',  'X'),
  ('someone@y', 'agent', 'real-estate', 'guardrail.blocked', 'block', 'Y'),
  ('plainx@x',  'agent', 'real-estate', 'attestation.pass',  'pass',  null);

-- Helper: assert a visible-count under a given jwt claim.
create or replace function pg_temp.assert_count(claims jsonb, expected int, label text)
returns void language plpgsql as $$
declare n int;
begin
  perform set_config('request.jwt.claims', claims::text, true);
  execute 'select count(*) from public.governance_events' into n;
  if n <> expected then
    raise exception 'FAIL %: expected % visible, got %', label, expected, n;
  end if;
  raise notice 'PASS %: % visible', label, n;
end $$;

set local role authenticated;

-- (1) account_admin of X sees the 2 X events, not Y, not the null historical.
select pg_temp.assert_count(
  '{"sub":"00000000-0000-0000-0000-0000000000a1","email":"admin@x","tags":[]}'::jsonb,
  2, 'account_admin sees own-instance only');

-- (2) member of X with governance_view=true sees the 2 X events.
select pg_temp.assert_count(
  '{"sub":"00000000-0000-0000-0000-0000000000a2","email":"mgr@x","tags":[]}'::jsonb,
  2, 'flagged manager sees own-instance');

-- (3) member of X with governance_view=false sees ONLY own-email rows (none here).
select pg_temp.assert_count(
  '{"sub":"00000000-0000-0000-0000-0000000000a3","email":"nobody@x","tags":[]}'::jsonb,
  0, 'unflagged member sees only own-email');

-- (4) compliance-tagged user sees ALL 4 events (both instances + the null row).
select pg_temp.assert_count(
  '{"sub":"00000000-0000-0000-0000-0000000000a1","email":"compliance@jnow","tags":["compliance"]}'::jsonb,
  4, 'compliance sees all instances');

-- (5) plain user with a matching email sees the historical own-email row.
select pg_temp.assert_count(
  '{"sub":"00000000-0000-0000-0000-0000000000a9","email":"plainx@x","tags":[]}'::jsonb,
  1, 'own-email fallback sees the null historical row');

rollback;
```

- [ ] **Step 3: Apply the migration to a local/scratch Supabase**

Run `0017_governance_tenancy.sql` against a **local `supabase` instance** (or a scratch project — NOT prod). Expected: no errors; `governance_events.instance_id` and `user_roles.governance_view` now exist and the `governance_events_select` policy is the new form.

- [ ] **Step 4: Run the RLS verification and confirm all PASS**

Run `.../supabase/tests/rls_0017_governance_tenancy.sql` in the same instance.
Expected: five `NOTICE: PASS …` lines, transaction rolls back, and no `FAIL`/`exception`. If any assertion raises, the RLS is wrong — fix the policy in Step 1 and re-run.

- [ ] **Step 5: Commit**

In the `D:\workspaces\jnow-site` worktree (branch `governance-2b`):

```bash
git add development/core/supabase/migrations/0017_governance_tenancy.sql \
        development/core/supabase/tests/rls_0017_governance_tenancy.sql
git commit -m "feat(governance): migration 0017 — instance-scoped governance_events RLS

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 2: Orchestrator — stamp `instance_id` on every governance write

**Files:**
- Modify: `src/api/runs.py` (add `_instance_id` helper near `_write_event` ~line 205; edit `_emit_guardrail` ~line 217; edit the two `_write_event` calls in the events handler's `gen()` ~lines 395 & 411, computing `inst` where `url`/`key` are read ~line 360)
- Modify: `src/api/admin.py` (edit `_emit_admin_event` ~line 151 to include `instance_id`)
- Test: `src/api/test_runs_guardrail.py` (guardrail + attestation writes carry `instance_id`)
- Test: `tests/test_admin_api.py` (admin audit write carries `instance_id`)

**Interfaces:**
- Consumes: `cfg.instance_id` from `request.app.state.config` (`Config.instance_id`, loaded from `INSTANCE_ID`, defaults `"default"`); the existing `_write_event(row, url, key)` transport (unchanged signature).
- Produces: every `governance_events` insert row now contains key `"instance_id"`. No new public functions consumed by other tasks.

**Design note (why not a big refactor):** the four write sites (`_emit_guardrail`, the two `gen()` `_write_event` calls, and admin's `_emit_admin_event`) each build their own row dict, and the low-level `_write_event`/`httpx.post` transport has no access to `cfg`. Rather than re-thread `cfg` through the transport (which would change `_write_event`'s signature and break the tests that monkeypatch it as `lambda row, url, key`), we add one small `_instance_id(request)` reader and stamp the column at each enumerated builder, with a test per site guaranteeing no site is missed. Admin audit rows are stamped too (confirmed in-scope) so an `account_admin` sees their own instance's admin audit trail under RLS clause (b).

- [ ] **Step 1: Write the failing tests (guardrail + attestation)**

In `src/api/test_runs_guardrail.py`, add these tests (they mirror the existing `test_blocked_prompt_returns_403…` / `test_governed_pass_attestation…` style — monkeypatch `_write_event` to capture rows, and monkeypatch `runs._instance_id` to a fixed value):

```python
def test_blocked_event_carries_instance_id(client, monkeypatch):
    written = []
    monkeypatch.setattr(runs, "_write_event", lambda row, url, key: written.append(row))
    monkeypatch.setattr(runs, "_instance_id", lambda request: "sandbox")
    body = json.dumps({"input": "how do i kill myself"}).encode()
    r = client.post(
        "/v1/runs/real-estate",
        content=body,
        headers={"X-Auth-Email": "user@example.com", "X-Auth-Role": "broker"},
    )
    assert r.status_code == 403
    assert len(written) == 1
    assert written[0]["instance_id"] == "sandbox"


def test_attestation_event_carries_instance_id(client, monkeypatch):
    written: list[dict] = []
    output_with_att = f"Listing copy here.\n{_ATT_PASS_BLOCK}"

    async def fake_stream(base, run_id):
        yield _sse(output_with_att)

    monkeypatch.setattr(runs, "_stream_upstream", fake_stream)
    monkeypatch.setattr(runs, "_write_event", lambda row, url, key: written.append(row))
    monkeypatch.setattr(runs, "_instance_id", lambda request: "sandbox")
    monkeypatch.delenv("GUARDRAIL_ENFORCE_APPS", raising=False)
    r = client.get(
        "/v1/runs/real-estate/r-1/events",
        headers={"X-Auth-Email": "broker@example.com", "X-Auth-Role": "broker",
                 "X-Gov-App": "real-estate"},
    )
    assert r.status_code == 200
    assert len(written) == 1
    assert written[0]["instance_id"] == "sandbox"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest src/api/test_runs_guardrail.py::test_blocked_event_carries_instance_id src/api/test_runs_guardrail.py::test_attestation_event_carries_instance_id -v`
Expected: FAIL — `KeyError: 'instance_id'` (the row has no such key) or `AttributeError: … has no attribute '_instance_id'`.

- [ ] **Step 3: Add the `_instance_id` helper and stamp the runs.py writes**

In `src/api/runs.py`, add the helper immediately after `_write_event` (after line 204):

```python
def _instance_id(request: Request) -> str | None:
    """The orchestrator's configured instance id (INSTANCE_ID), or None on a bare
    scope / missing config. Governance writes stamp this so RLS can scope reads."""
    try:
        cfg = getattr(request.app.state, "config", None)
        return cfg.instance_id if cfg else None
    except Exception:
        return None
```

In `_emit_guardrail` (the `_write_event({...})` dict at lines 228-238), add the `instance_id` key:

```python
        _write_event({
            "user_email": email,
            "user_role": role,
            "app": agent,
            "event_type": event_type,
            "status": verdict.get("decision"),
            "title": verdict.get("citation"),
            "findings": verdict.get("prohibition"),
            "content": safe_content,
            "run_id": None,
            "instance_id": _instance_id(request),
        }, url, key)
```

In the events handler, where `url`/`key` are read (after line 361), add:

```python
    inst = _instance_id(request)
```

Then add `"instance_id": inst,` to BOTH `_write_event({...})` calls inside `gen()` — the enforcement record (lines 395-405) and the rich-capture record (lines 411-417). Each dict gains the same trailing key:

```python
                    _write_event({
                        "user_email": email, "user_role": role,
                        "app": gov_app, "event_type": d["event_type"],
                        "status": d["action"],
                        "title": None,
                        "findings": (att or {}).get("rules") or [],
                        "content": None,
                        "run_id": run_id,
                        "instance_id": inst,
                    }, url, key)
```

```python
                            _write_event({
                                "user_email": email, "user_role": role,
                                "app": gov_app, "event_type": gov_event,
                                "status": parsed["status"], "title": gov_title or None,
                                "findings": parsed["findings"], "content": parsed["content"],
                                "run_id": run_id,
                                "instance_id": inst,
                            }, url, key)
```

- [ ] **Step 4: Run the runs.py tests to verify they pass**

Run: `python -m pytest src/api/test_runs_guardrail.py -v`
Expected: PASS for the two new tests AND all pre-existing guardrail/attestation tests (they don't assert exact dict equality, so the added key is harmless).

- [ ] **Step 5: Write the failing test (admin audit write)**

In `tests/test_admin_api.py`, add a test that the admin audit `governance_events` POST carries `instance_id` (mirrors `test_emit_admin_event_records_actual_caller_tier`, which captures the posted JSON via a fake `httpx.post`):

```python
def test_admin_event_carries_instance_id(client, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier",
                        lambda i, u: "member" if u == MEMBER else "account_admin")
    monkeypatch.setattr(roles, "set_tier", lambda inst, uid, tier, by: None)
    posted = {}

    class _Resp:
        def raise_for_status(self):
            pass

    def _fake_post(url, headers=None, json=None, timeout=None):
        posted["json"] = json
        return _Resp()

    monkeypatch.setenv("SUPABASE_URL", "http://sb")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "k")
    monkeypatch.setattr(admin.httpx, "post", _fake_post)
    r = client.put(f"/v1/admin/users/{MEMBER}/role", json={"tier": "manager"},
                   headers={"X-Auth-User-Id": ADMIN})
    assert r.status_code == 200
    assert posted["json"]["instance_id"] == "sandbox"  # fixture app.state.config
```

- [ ] **Step 6: Run it to verify it fails**

Run: `python -m pytest tests/test_admin_api.py::test_admin_event_carries_instance_id -v`
Expected: FAIL — `KeyError: 'instance_id'` (the posted JSON lacks the key).

- [ ] **Step 7: Stamp the admin audit write**

In `src/api/admin.py`, change `_emit_admin_event`'s signature to accept `request` (it already does) and add `instance_id` to the posted JSON (the `json={...}` dict at lines 159-162). Read the instance id from config:

```python
def _emit_admin_event(request, event_type, target_user, detail, actor, actor_tier) -> None:
    """Best-effort governance event for an admin write. Never raises."""
    try:
        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not (url and key):
            return
        try:
            instance_id = request.app.state.config.instance_id
        except Exception:
            instance_id = None
        httpx.post(
            f"{url}/rest/v1/governance_events",
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Content-Type": "application/json", "Prefer": "return=minimal"},
            json={"user_email": actor or "", "user_role": actor_tier,
                  "app": "admin", "event_type": event_type, "status": "ok",
                  "title": target_user, "findings": [], "content": detail,
                  "run_id": None, "instance_id": instance_id},
            timeout=10.0,
        ).raise_for_status()
    except Exception:
        _logger.warning("_emit_admin_event failed", exc_info=True)
```

- [ ] **Step 8: Run the admin test to verify it passes**

Run: `python -m pytest tests/test_admin_api.py::test_admin_event_carries_instance_id -v`
Expected: PASS.

- [ ] **Step 9: Run the full orchestrator suite (no new failures)**

Run: `python -m pytest tests/ src/ -q`
Expected: **17 failed / 310 passed** (baseline 17 failed / 308 passed + the 2 new runs.py tests + the 1 new admin test = 311 passed; exact count may vary — the invariant is **no NEW failures beyond the 17 Windows baseline**).

- [ ] **Step 10: Commit**

```bash
git add src/api/runs.py src/api/admin.py src/api/test_runs_guardrail.py tests/test_admin_api.py
git commit -m "feat(governance): stamp instance_id on every governance_events write

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 3: Orchestrator — `governanceView` in whoami + admin toggle verb

**Files:**
- Modify: `src/api/roles.py` (add `_gov_cache`, `_fetch_gov`, `resolve_governance_view`, `set_governance_view`; extend `invalidate_cache` to sweep the gov cache)
- Modify: `src/api/admin.py` (add `governanceView` to `whoami`; add `GovernanceViewBody` + `PUT /v1/admin/users/{user_id}/governance-view`)
- Test: `tests/test_roles.py`, `tests/test_admin_api.py`

**Interfaces:**
- Consumes: `user_roles.governance_view` (Task 1); `roles.is_at_least`, `roles._sb`, `roles._sb_headers`, `roles._now_iso`, `roles._CACHE_TTL`, `roles.invalidate_cache` (existing); `_require_admin(request) -> ((uid, tier), None) | (None, response)`, `_cfg(request)`, `_emit_admin_event(request, event_type, target_user, detail, actor, actor_tier)` (existing, admin.py).
- Produces:
  - `roles.resolve_governance_view(instance_id: str, user_id: str) -> bool` — True iff the user's `user_roles` row for `instance_id` has tier `account_admin`+ OR `governance_view = true`; cached (`_CACHE_TTL`), fail-closed `False`.
  - `roles.set_governance_view(instance_id: str, user_id: str, enabled: bool) -> None` — upserts the flag without clobbering an existing tier (ensure-row via `on_conflict do nothing` insert of tier `member`, then PATCH the flag).
  - `whoami` response gains `"governanceView": bool`.
  - `PUT /v1/admin/users/{user_id}/governance-view` (account_admin+; body `{"enabled": bool}`; per caller's instance; emits `governance_view.set` audit event) → `{"userId": …, "governanceView": bool}`. Consumed by Task 4's frontend.

- [ ] **Step 1: Write the failing tests for roles.py**

In `tests/test_roles.py` (fixture already sets `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` and clears caches), add:

```python
def test_resolve_governance_view_true_for_account_admin(monkeypatch):
    monkeypatch.setattr(roles, "_fetch_gov", lambda inst, uid: True)
    roles.invalidate_cache()
    assert roles.resolve_governance_view(INST, U) is True


def test_resolve_governance_view_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(roles, "_fetch_gov", lambda inst, uid: calls.append(1) or True)
    roles.invalidate_cache()
    roles.resolve_governance_view(INST, U)
    roles.resolve_governance_view(INST, U)
    assert len(calls) == 1               # second served from cache
    roles.invalidate_cache(U)
    roles.resolve_governance_view(INST, U)
    assert len(calls) == 2               # invalidate_cache also sweeps the gov cache


def test_resolve_governance_view_fails_closed(monkeypatch):
    def boom(inst, uid):
        raise RuntimeError("supabase down")
    monkeypatch.setattr(roles, "_fetch_gov", boom)
    roles.invalidate_cache()
    assert roles.resolve_governance_view(INST, U) is False


def test_set_governance_view_ensures_row_then_patches(monkeypatch):
    calls = []

    class _Resp:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(roles.httpx, "post",
                        lambda *a, **k: calls.append(("post", k.get("json"))) or _Resp())
    monkeypatch.setattr(roles.httpx, "patch",
                        lambda *a, **k: calls.append(("patch", k.get("json"))) or _Resp())
    roles.set_governance_view(INST, U, True)
    # ensure-row insert of tier 'member' (no-clobber), then PATCH the flag.
    assert calls[0][0] == "post" and calls[0][1]["tier"] == "member"
    assert calls[1][0] == "patch" and calls[1][1]["governance_view"] is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_roles.py -k governance_view -v`
Expected: FAIL — `AttributeError: module 'src.api.roles' has no attribute 'resolve_governance_view'` / `set_governance_view` / `_fetch_gov`.

- [ ] **Step 3: Implement the roles.py additions**

In `src/api/roles.py`, add the gov cache near the tier cache (after the `_tier_cache` definition ~line 26):

```python
# (instance_id, user_id) -> (governance_view_bool, monotonic_expiry)
_gov_cache: dict[tuple[str, str], tuple[bool, float]] = {}
```

Add the fetch + resolver (near `resolve_tier`/`_fetch_tier`):

```python
def _fetch_gov(instance_id: str, user_id: str) -> bool:
    """True iff the user's user_roles row for instance_id grants governance view:
    account_admin+ tier OR the governance_view flag. False if no row."""
    sb = _sb()
    if not sb:
        return False
    url, key = sb
    resp = httpx.get(
        f"{url}/rest/v1/user_roles",
        params={"instance_id": f"eq.{instance_id}", "user_id": f"eq.{user_id}",
                "select": "tier,governance_view"},
        headers=_sb_headers(key), timeout=10.0,
    )
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return False
    row = rows[0]
    return is_at_least(row.get("tier") or "member", "account_admin") or bool(row.get("governance_view"))


def resolve_governance_view(instance_id: str, user_id: str) -> bool:
    """Whether the caller may see this instance's governance events; cached;
    fail-closed to False on absence or any error."""
    now = time.monotonic()
    key = (instance_id, user_id)
    hit = _gov_cache.get(key)
    if hit and hit[1] > now:
        return hit[0]
    try:
        gv = _fetch_gov(instance_id, user_id)
    except Exception:
        _logger.warning("resolve_governance_view failed; defaulting False", exc_info=True)
        gv = False
    _gov_cache[key] = (gv, now + _CACHE_TTL)
    return gv
```

Add the writer (near `set_tier`):

```python
def set_governance_view(instance_id: str, user_id: str, enabled: bool) -> None:
    """Set a user's per-instance governance_view flag WITHOUT clobbering their tier.
    Ensure a row exists (on_conflict do nothing, tier=member), then PATCH the flag."""
    sb = _sb()
    if not sb:
        raise RuntimeError("Supabase not configured")
    url, key = sb
    # 1. Ensure a row exists; on conflict do nothing so an existing tier is untouched.
    httpx.post(
        f"{url}/rest/v1/user_roles",
        params={"on_conflict": "instance_id,user_id"},
        headers={**_sb_headers(key), "Prefer": "resolution=ignore-duplicates,return=minimal"},
        json={"instance_id": instance_id, "user_id": user_id, "tier": "member"},
        timeout=10.0,
    ).raise_for_status()
    # 2. Set the flag only (never touches tier).
    httpx.patch(
        f"{url}/rest/v1/user_roles",
        params={"instance_id": f"eq.{instance_id}", "user_id": f"eq.{user_id}"},
        headers={**_sb_headers(key), "Prefer": "return=minimal"},
        json={"governance_view": enabled, "updated_at": _now_iso()},
        timeout=10.0,
    ).raise_for_status()
    invalidate_cache(user_id)
```

Extend `invalidate_cache` so it also sweeps the gov cache (tier and gov are correlated — an account_admin promotion changes both). Change it to:

```python
def invalidate_cache(user_id: str | None = None) -> None:
    if user_id is None:
        _tier_cache.clear()
        _gov_cache.clear()
    else:
        for k in [k for k in _tier_cache if k[1] == user_id]:
            _tier_cache.pop(k, None)
        for k in [k for k in _gov_cache if k[1] == user_id]:
            _gov_cache.pop(k, None)
```

- [ ] **Step 4: Run the roles.py tests to verify they pass**

Run: `python -m pytest tests/test_roles.py -k governance_view -v`
Expected: PASS (all four).

- [ ] **Step 5: Write the failing tests for whoami + the admin verb**

In `tests/test_admin_api.py`, add:

```python
def test_whoami_includes_governance_view(client, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "member")
    monkeypatch.setattr(roles, "get_labels", lambda i: dict(roles.DEFAULT_LABELS))
    monkeypatch.setattr(roles, "list_user_tags", lambda u: [])
    monkeypatch.setattr(roles, "resolve_governance_view", lambda i, u: True)
    r = client.get("/v1/whoami", headers={"X-Auth-User-Id": MEMBER})
    assert r.status_code == 200
    assert r.json()["governanceView"] is True


def test_set_governance_view_admin_only_and_audits(client, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "account_admin")
    writes, events = [], []
    monkeypatch.setattr(roles, "set_governance_view",
                        lambda inst, uid, enabled: writes.append((inst, uid, enabled)))
    monkeypatch.setattr(admin, "_emit_admin_event", lambda *a, **k: events.append(a))
    r = client.put(f"/v1/admin/users/{MEMBER}/governance-view", json={"enabled": True},
                   headers={"X-Auth-User-Id": ADMIN})
    assert r.status_code == 200
    assert writes == [("sandbox", MEMBER, True)]
    assert len(events) == 1


def test_set_governance_view_forbidden_for_member(client, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "member")
    monkeypatch.setattr(roles, "set_governance_view",
                        lambda *a: pytest.fail("member must not write"))
    r = client.put(f"/v1/admin/users/{MEMBER}/governance-view", json={"enabled": True},
                   headers={"X-Auth-User-Id": MEMBER})
    assert r.status_code == 403
```

(If `pytest` isn't already imported in this test module, add `import pytest` at the top.)

- [ ] **Step 6: Run to verify they fail**

Run: `python -m pytest tests/test_admin_api.py -k governance_view -v`
Expected: FAIL — whoami has no `governanceView` key (KeyError); the `PUT …/governance-view` route 404s (not yet defined).

- [ ] **Step 7: Implement the whoami field and the admin verb**

In `src/api/admin.py`, extend the `whoami` return dict (lines 51-54) to include `governanceView`:

```python
@router.get("/v1/whoami")
def whoami(request: Request):
    uid = _uid(request)
    if not uid:
        return _UNAUTH
    cfg = _cfg(request)
    tier = roles.resolve_tier(cfg.instance_id, uid)
    label = roles.get_labels(cfg.instance_id).get(tier, tier)
    return {"userId": uid, "tier": tier, "label": label,
            "tags": roles.list_user_tags(uid),
            "governanceView": roles.resolve_governance_view(cfg.instance_id, uid),
            "reachableAgentIds": authz.reachable_agent_ids(request, cfg)}
```

Add the request-body model next to the existing `RoleBody`/`TagsBody` definitions:

```python
class GovernanceViewBody(BaseModel):
    enabled: bool
```

Add the verb near `set_user_tags_route` (no tier-escalation guard — `governance_view` is a visibility grant, not authority, per spec §5.3; `_require_admin` gating is sufficient):

```python
@router.put("/v1/admin/users/{user_id}/governance-view")
def set_user_governance_view(user_id: str, body: GovernanceViewBody, request: Request):
    caller, deny = _require_admin(request)
    if deny:
        return deny
    roles.set_governance_view(_cfg(request).instance_id, user_id, body.enabled)
    _emit_admin_event(request, "governance_view.set", user_id, str(body.enabled),
                      caller[0], caller[1])
    return {"userId": user_id, "governanceView": body.enabled}
```

- [ ] **Step 8: Fix the existing whoami equality test**

The existing `test_whoami_returns_tier_and_reachable` asserts full-dict equality and will now fail (missing `governanceView`). Update it to monkeypatch `resolve_governance_view` and include the key:

```python
def test_whoami_returns_tier_and_reachable(client, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "member")
    monkeypatch.setattr(roles, "get_labels", lambda i: dict(roles.DEFAULT_LABELS))
    monkeypatch.setattr(roles, "resolve_governance_view", lambda i, u: False)
    r = client.get("/v1/whoami", headers={"X-Auth-User-Id": MEMBER})
    assert r.status_code == 200
    assert r.json() == {"userId": MEMBER, "tier": "member",
                        "label": "Member", "tags": [],
                        "governanceView": False,
                        "reachableAgentIds": ["default"]}
```

- [ ] **Step 9: Run the admin tests to verify they pass**

Run: `python -m pytest tests/test_admin_api.py -v`
Expected: PASS (new governance-view tests + the updated whoami equality test + all existing admin tests).

- [ ] **Step 10: Run the full orchestrator suite (no new failures)**

Run: `python -m pytest tests/ src/ -q`
Expected: no NEW failures beyond the 17 Windows baseline.

- [ ] **Step 11: Commit**

```bash
git add src/api/roles.py src/api/admin.py tests/test_roles.py tests/test_admin_api.py
git commit -m "feat(governance): whoami.governanceView + PUT admin governance-view verb

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 4: Frontend — `useIdentity.governanceView` + Compliance nav/route gating

**Files:**
- Modify: `src/hooks/useIdentity.ts`
- Modify: `src/adapters/orchestrator/OrchestratorTypes.ts`
- Modify: `src/components/RoleRoute.tsx`
- Modify: `src/components/Layout.tsx`
- Modify: `src/App.tsx`
- Test: `src/hooks/__tests__/useIdentity.test.ts`, `src/components/RoleRoute.test.tsx`, `src/components/Layout.test.tsx`, `src/components/Layout.navgating.test.tsx`

**Interfaces:**
- Consumes: the orchestrator `whoami` response's new `governanceView` bool (Task 3), fetched by `useIdentity` from `/orchestrator-proxy/v1/whoami`.
- Produces: `Identity.governanceView: boolean`; a `governanceView?: boolean` prop/axis on `RoleRoute` and `NavItemDef` that ORs `id.governanceView` into the gate **only** for items that set it (scoping: `governance_view` grants governance visibility only, NOT management authority — so it must NOT leak into Skills/Schedules/Logs/Usage, which are `minTier: account_admin` with no `governanceView`).

**IMPORTANT — breaking type change:** adding a required `governanceView: boolean` field to `Identity` makes every test that mocks `useIdentity` with a full object fail `tsc` until the field is added. After editing the interface, grep for all `useIdentity` mocks and add `governanceView: false` to each:
`grep -rn "reachableAgentIds" src --include=*.test.tsx --include=*.test.ts` (each full mock object needs the new key).

- [ ] **Step 1: Write the failing test for useIdentity**

In `src/hooks/__tests__/useIdentity.test.ts`, add (mirrors the existing `loads tier + tags from whoami` test):

```typescript
  it('loads governanceView from whoami and defaults it false when absent', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ tier: 'member', tags: [], reachableAgentIds: [], governanceView: true }),
    }) as unknown as typeof fetch;
    __resetIdentityCache();
    const { result } = renderHook(() => useIdentity());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.governanceView).toBe(true);
  });
```

- [ ] **Step 2: Run it to verify it fails**

Run (Git Bash): `npx vitest run src/hooks/__tests__/useIdentity.test.ts`
Expected: FAIL — `result.current.governanceView` is `undefined` (property doesn't exist).

- [ ] **Step 3: Add `governanceView` to `useIdentity`**

In `src/hooks/useIdentity.ts`: add the field to the interface, both fail-safe returns, the `load()` success return, and the `useState` initial value:

```typescript
export interface Identity {
  tier: Tier | null;
  tags: string[];
  reachableAgentIds: string[];
  governanceView: boolean;
  loading: boolean;
}
```

```typescript
async function load(): Promise<Omit<Identity, 'loading'>> {
  try {
    const res = await fetch('/orchestrator-proxy/v1/whoami');
    if (!res.ok) return { tier: null, tags: [], reachableAgentIds: [], governanceView: false };
    const d = await res.json();
    return {
      tier: (d.tier ?? null) as Tier | null,
      tags: Array.isArray(d.tags) ? d.tags : [],
      reachableAgentIds: Array.isArray(d.reachableAgentIds) ? d.reachableAgentIds : [],
      governanceView: d.governanceView === true,
    };
  } catch {
    return { tier: null, tags: [], reachableAgentIds: [], governanceView: false };
  }
}
```

```typescript
  const [state, setState] = useState<Identity>({ tier: null, tags: [], reachableAgentIds: [], governanceView: false, loading: true });
```

- [ ] **Step 4: Run the useIdentity test to verify it passes**

Run (Git Bash): `npx vitest run src/hooks/__tests__/useIdentity.test.ts`
Expected: PASS.

- [ ] **Step 5: Add `governanceView` to `WhoamiResponse` (type honesty)**

In `src/adapters/orchestrator/OrchestratorTypes.ts`, add the optional field to `WhoamiResponse` (keeps the dual whoami paths from drifting further; also add `tags?` if still missing):

```typescript
export interface WhoamiResponse {
  userId: string;
  tier: string;
  label: string;
  tags?: string[];
  governanceView?: boolean;
  reachableAgentIds: string[];
}
```

- [ ] **Step 6: Write the failing test for RoleRoute governanceView**

In `src/components/RoleRoute.test.tsx`, add (mirrors the existing tag-based test; also add `governanceView` to that file's existing `mockReturnValue` objects — see Step 9):

```typescript
  it('renders the element for a governanceView user when the route opts in', () => {
    vi.mocked(useIdentity).mockReturnValue({ tier: 'member', tags: [], reachableAgentIds: [], governanceView: true, loading: false })
    renderAt({ minTier: 'account_admin', anyTag: ['compliance'], governanceView: true })
    expect(screen.getByText('secret page')).toBeTruthy()
  })

  it('does NOT render for a governanceView user when the route did not opt in', () => {
    vi.mocked(useIdentity).mockReturnValue({ tier: 'member', tags: [], reachableAgentIds: [], governanceView: true, loading: false })
    renderAt({ minTier: 'account_admin' })
    expect(screen.queryByText('secret page')).toBeNull()
  })
```

(If `renderAt` doesn't forward a `governanceView` prop, extend its props type to include `governanceView?: boolean` and pass it through to `RoleRoute`.)

- [ ] **Step 7: Run it to verify it fails**

Run (Git Bash): `npx vitest run src/components/RoleRoute.test.tsx`
Expected: FAIL — `RoleRoute` has no `governanceView` prop, so the opt-in route redirects (element not found) / tsc rejects the prop.

- [ ] **Step 8: Add the `governanceView` axis to RoleRoute**

In `src/components/RoleRoute.tsx`:

```typescript
export default function RoleRoute({
  minTier,
  anyTag,
  governanceView,
  element,
}: {
  minTier?: Tier;
  anyTag?: string[];
  governanceView?: boolean;
  element: ReactElement;
}): ReactElement | null {
  const id = useIdentity();
  if (id.loading) return null;
  const tierOk = minTier != null && atLeast(id.tier, minTier);
  const tagOk = anyTag != null && anyTag.some(t => id.tags.includes(t));
  const govOk = governanceView === true && id.governanceView === true;
  const allowed = (minTier == null && anyTag == null && !governanceView) ? true : (tierOk || tagOk || govOk);
  return allowed ? element : <Navigate to="/" replace />
}
```

- [ ] **Step 9: Add `governanceView: false` to RoleRoute.test's existing mocks, run RoleRoute tests**

In every existing `vi.mocked(useIdentity).mockReturnValue({...})` in `src/components/RoleRoute.test.tsx`, add `governanceView: false` (or `true` where the new tests need it). Then:

Run (Git Bash): `npx vitest run src/components/RoleRoute.test.tsx`
Expected: PASS (existing + 2 new).

- [ ] **Step 10: Add the `governanceView` axis to Layout nav gating**

In `src/components/Layout.tsx`:

Add the field to `NavItemDef`:

```typescript
interface NavItemDef {
  label: string;
  to: string;
  icon: ReactNode;
  capability?: Capability;
  requiresSupabase?: boolean;
  minTier?: Tier;
  anyTag?: string[];
  governanceView?: boolean;
}
```

Set `governanceView: true` on the three compliance items in `ALL_ITEMS`:

```typescript
  { label: 'Compliance', to: '/compliance', icon: <FileCheck size={15} />, requiresSupabase: true, minTier: 'account_admin', anyTag: ['compliance'], governanceView: true },
  { label: 'Verification', to: '/verification', icon: <ListChecks size={15} />, requiresSupabase: true, minTier: 'account_admin', anyTag: ['compliance'], governanceView: true },
  { label: 'TRAIGA Report', to: '/traiga-report', icon: <ShieldCheck size={15} />, requiresSupabase: true, minTier: 'account_admin', anyTag: ['compliance'], governanceView: true },
```

In `NavItemRow`, OR the flag in (only when the item opts in):

```typescript
  const tierOk = item.minTier != null && atLeast(id.tier, item.minTier);
  const tagOk = item.anyTag != null && item.anyTag.some(t => id.tags.includes(t));
  const govOk = item.governanceView === true && id.governanceView === true;
  const roleGateOk = (!item.minTier && !item.anyTag && !item.governanceView) ? true : (tierOk || tagOk || govOk);
```

- [ ] **Step 11: Pass `governanceView` on the three compliance routes**

In `src/App.tsx`:

```typescript
          <Route path="/compliance" element={<RoleRoute minTier="account_admin" anyTag={['compliance']} governanceView element={<Compliance />} />} />
          <Route path="/verification" element={<RoleRoute minTier="account_admin" anyTag={['compliance']} governanceView element={<Verification />} />} />
          <Route path="/traiga-report" element={<RoleRoute minTier="account_admin" anyTag={['compliance']} governanceView element={<TraigaReport />} />} />
```

- [ ] **Step 12: Update Layout test mocks + add a nav-gating assertion**

In `src/components/Layout.test.tsx`, add `governanceView: false` to the module-level `useIdentity` mock object. In `src/components/Layout.navgating.test.tsx`, add an assertion that the three compliance items carry `governanceView: true`:

```typescript
  it('opts the compliance nav items into the governanceView axis', () => {
    for (const to of ['/compliance', '/verification', '/traiga-report']) {
      const item = ALL_ITEMS.find(i => i.to === to);
      expect(item, `${to} nav item exists`).toBeTruthy();
      expect(item!.governanceView, `${to} honors governanceView`).toBe(true);
    }
  });
```

- [ ] **Step 13: Run the full frontend suite + typecheck**

Run (Git Bash): `npx vitest run` then `npx tsc --noEmit`
Expected: all tests pass; `tsc` clean (every `useIdentity` mock now includes `governanceView`).

- [ ] **Step 14: Commit**

```bash
git add src/hooks/useIdentity.ts src/adapters/orchestrator/OrchestratorTypes.ts \
        src/components/RoleRoute.tsx src/components/Layout.tsx src/App.tsx \
        src/hooks/__tests__/useIdentity.test.ts src/components/RoleRoute.test.tsx \
        src/components/Layout.test.tsx src/components/Layout.navgating.test.tsx
git commit -m "feat(governance): gate Compliance nav/routes on governanceView

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 5: Rollout runbook

**Files:**
- Create: `docs/runbooks/governance-tenancy-2b-rollout.md` (orchestrator repo)

**Interfaces:**
- Consumes: the artifacts of Tasks 1-4 (migration `0017`, orchestrator instance_id stamping + whoami/admin verb, frontend nav gate).
- Produces: an ordered, sandbox-first deploy runbook with smoke tests and rollback.

- [ ] **Step 1: Write the runbook**

Create `docs/runbooks/governance-tenancy-2b-rollout.md` covering, in order:

1. **Preconditions.** Phase 2 is live on the target box; `INSTANCE_ID` is set on the box (`sandbox` on the sandbox box). jnow pre-flight (from `HANDOFF-governance-2b.md`): `git pull` the install repo (`8f37bdf`) and verify `~/hermes-stack/.env` has `SUPABASE_URL`/`SUPABASE_ANON_KEY`/`SUPABASE_COOKIE_DOMAIN` populated BEFORE any frontend recreate.
2. **Ordering (do NOT reorder).** (a) Deploy the orchestrator (Task 2/3 build) so new events start carrying `instance_id` while the old `0016` policy is still active (it ignores the column) — safe accumulation. (b) Apply `0017` (columns + flag + new RLS). (c) Deploy the frontend (`governanceView` nav gate). Note the shared-table caveat: applying `0017` affects BOTH boxes; during the sandbox-deployed-but-jnow-not window, jnow's new events lack `instance_id`, so jnow brokers see governance only via compliance-tag/own-email until jnow's orchestrator deploys — reduced visibility, not a leak; keep the window short.
3. **Sandbox-first, then jnow.** SSH via Windows PowerShell `ssh -F NUL ollie@178.105.216.167` with base64-piped scripts (1Password prompt needs John).
4. **Smoke tests (§8 of the spec):**
   - `whoami` for the account_admin returns `governanceView: true`; for a plain member `false`.
   - `PUT /v1/admin/users/{id}/governance-view {"enabled":true}` as an account_admin → 200; as a member → 403; after enabling, that user's `whoami` returns `governanceView: true`.
   - In the Compliance page: the owner (account_admin) sees their instance's events; a flagged manager sees them; a plain member does not see the nav item.
   - RLS spot-check (optional, non-prod): run `tests/rls_0017_governance_tenancy.sql`.
5. **Rollback.** Re-apply `0016`'s `governance_events_select` policy (copied verbatim in the runbook), revert the orchestrator + frontend to their pre-2b images/SHAs. The added `instance_id`/`governance_view` columns are inert and may stay.

- [ ] **Step 2: Review the runbook for completeness**

Confirm every §8 smoke test and the exact `0016` rollback policy are present; verify the ordering matches Global Constraints. (No automated test — this is documentation; the "test" is the completeness check.)

- [ ] **Step 3: Commit**

```bash
git add docs/runbooks/governance-tenancy-2b-rollout.md
git commit -m "docs(governance): Phase 2b staged rollout runbook

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage (§ → task):**
- §3a `instance_id` column → Task 1 Step 1. §3b `governance_view` column → Task 1 Step 1. ✓
- §4 RLS rewrite (compliance tag OR per-instance tier/grant OR own-email) → Task 1 Step 1, verified Task 1 Step 4. ✓
- §5.1 stamp `instance_id` on every governance write (guardrail, attestation, + admin audit) → Task 2. ✓
- §5.2 `whoami.governanceView` → Task 3 Steps 7-8. ✓
- §5.3 `PUT …/governance-view` (account_admin+, per-instance, audit event, no escalation guard) → Task 3 Step 7. ✓
- §6.1 `useIdentity.governanceView` → Task 4 Step 3. §6.2 Compliance/Verification/TRAIGA nav+routes gate on it → Task 4 Steps 8-11. §6.3 no page-content change → respected (no Compliance page edits). ✓
- §7 rollout ordering (orchestrator → 0017 → frontend), sandbox-first, jnow pre-flight, rollback → Task 5. ✓
- §8 testing spine → RLS (Task 1 Step 2/4), orchestrator (Task 2 + Task 3 tests), frontend (Task 4 tests), smoke (Task 5). ✓
- §10 traceability: cross-tenant-leak prevention = instance-scoped join (Task 1 §4 clause b); owner-never-configures = tier clause; additive/reversible = all columns `if not exists`/`default false` + policy swap. ✓

**Placeholder scan:** no TBD/TODO/"handle edge cases"/"similar to Task N" — every code step shows literal code; every run step shows the command + expected result. ✓

**Type consistency:** `resolve_governance_view(instance_id, user_id) -> bool`, `set_governance_view(instance_id, user_id, enabled) -> None`, `_fetch_gov(instance_id, user_id) -> bool`, `_instance_id(request) -> str | None`, `GovernanceViewBody.enabled: bool`, `Identity.governanceView: boolean`, `WhoamiResponse.governanceView?: boolean`, and the `governanceView?` prop/axis on `RoleRoute`/`NavItemDef` — names/signatures match across every task that references them. The whoami JSON key is `governanceView` everywhere (orchestrator return + frontend read). ✓

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-04-governance-tenancy-phase2b.md`.
