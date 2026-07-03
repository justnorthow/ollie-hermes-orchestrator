# Phase 2a.3 — Identity Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate the two role systems into one — per-instance permission tier (authority, orchestrator-resolved) + global functional tags (compliance/marketing, JWT-carried for governance) — served by one `whoami` and one frontend `useIdentity` hook, retiring `profiles.role` as the frontend authority source.

**Architecture:** Spec: `docs/superpowers/specs/2026-07-03-identity-consolidation-phase2a3-design.md`. A new GLOBAL `user_tags(user_id, tag)` table holds functional tags; the Supabase `custom_access_token_hook` additionally stamps a `tags` claim (tier is NEVER stamped — it stays per-instance in `resolve_tier`); `governance_events` RLS moves (staged, after the hook is proven) from the old `user_role` claim to `tags ? 'compliance' OR user_role='admin'`. The orchestrator's `roles.py`/`whoami`/admin API gain tags; the frontend collapses `useUserRole`+scattered whoami calls into one `useIdentity`.

**Tech Stack:** Supabase Postgres (migrations + plpgsql hook + RLS), Python 3.11 + FastAPI + pytest (orchestrator), TypeScript + React + vitest (frontend).

## Global Constraints

- **Tier is NEVER a JWT claim and NEVER global** — it stays per-instance, resolved by `roles.resolve_tier(instance_id, user_id)` (Phase 2a). This plan does not touch tier resolution. (Spec §3.)
- **Tags are GLOBAL** — `user_tags(user_id, tag)`, no `instance_id`. A tag grants NO authority; it only gates functional/nav visibility + governance read. (Spec §2, §3.)
- **The auth-hook + RLS changes are LOGIN-CRITICAL and STAGED.** A broken hook blocks all logins. Order: (1) additive hook emits `tags` while keeping `user_role`; verify logins on sandbox; (2) only then cut over governance RLS; (3) retiring `user_role` is deferred (kept this phase). Migration FILES are committed here; APPLYING them to Supabase is a runbook deploy step (sandbox-first), never auto-run. (Spec §5, §9.)
- **profiles.role DB values are `agent | compliance | marketing | admin`** (verified `0001_identity.sql`), NOT `broker`. Migration mapping: `admin → account_admin` tier; `agent`/`compliance`/`marketing` → `member` tier; `compliance`/`marketing` → the matching global tag. (Spec §4.)
- Orchestrator test cmd (Windows): `python -m pytest tests/ src/ -q` — 17 pre-existing Windows failures acceptable; nothing new. Frontend: `npx vitest run` (from a bash/sh shell — generate-*.sh tests need `sh`) + `npx tsc --noEmit`.
- Repos: orchestrator `master` at `D:\devprojects\ollie-hermes-orchestrator`; frontend `master` at `D:\devprojects\ollie-hermes-frontend`; migrations in the jnow-site worktree `D:\workspaces\jnow-site\development\core\supabase\migrations` (holds jnow-workspace `main` — NEVER `git switch main` in `D:\workspaces\jnow-workspace`). Next migration number is **0013** (0012 = user_roles).
- Commit after each task; end messages with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: `user_tags` global table (Supabase)

**Files:**
- Create: `D:\workspaces\jnow-site\development\core\supabase\migrations\0013_user_tags.sql`

**Interfaces:**
- Produces: global `public.user_tags(user_id, tag)` (PK `(user_id, tag)`), read by the hook (Task 5) and orchestrator (Task 3) via PostgREST/service role.

- [ ] **Step 1: Write the migration** (mirrors `0011`/`0012` style; adds the auth-admin read policy like `0009` did for profiles)

```sql
-- 0013_user_tags.sql — GLOBAL functional tags (compliance/marketing) — non-authority.
-- (identity consolidation Phase 2a.3; spec: ollie-hermes-orchestrator
-- docs/superpowers/specs/2026-07-03-identity-consolidation-phase2a3-design.md).
-- Global (no instance_id): a functional tag is an attribute of the person and
-- rides the instance-blind JWT for governance RLS. Authority (tier) is separate,
-- per-instance, and never a JWT claim.

create table if not exists public.user_tags (
  user_id     uuid not null,
  tag         text not null,
  created_at  timestamptz not null default now(),
  primary key (user_id, tag)
);

alter table public.user_tags enable row level security;

-- A user reads their own tags (defense in depth; orchestrator reads via service role).
create policy user_tags_select_own on public.user_tags
  for select to authenticated
  using (user_id = auth.uid());

-- The access-token hook runs as supabase_auth_admin and its SELECT is subject to RLS
-- (same reason as 0009 for profiles) — grant it a permissive read.
create policy user_tags_auth_admin_read on public.user_tags
  as permissive for select to supabase_auth_admin
  using (true);

-- No INSERT/UPDATE/DELETE policy → only the service role (orchestrator admin API) writes.

comment on table public.user_tags is
  'Phase 2a.3: GLOBAL functional tags (compliance/marketing); non-authority; JWT-carried for governance RLS.';
```

- [ ] **Step 2: Verify worktree + commit**

```bash
cd /d/workspaces/jnow-site
git branch --show-current   # expect: main; else STOP
git add development/core/supabase/migrations/0013_user_tags.sql
git commit -m "feat(core): global user_tags table (identity consolidation 2a.3)"
```

Applying to Supabase is a runbook step (Task 8), not this task.

---

### Task 2: `profiles.role` → global tags data migration (Supabase)

**Files:**
- Create: `D:\workspaces\jnow-site\development\core\supabase\migrations\0014_migrate_profiles_tags.sql`

**Interfaces:**
- Consumes: `profiles.role` (0001), `user_tags` (Task 1).
- Produces: one `user_tags` row per compliance/marketing user. (The per-instance TIER
  half of the migration is a parameterized runbook step, Task 8 — it needs an
  instance_id that `profiles` lacks.)

- [ ] **Step 1: Write the migration**

```sql
-- 0014_migrate_profiles_tags.sql — seed global tags from existing profiles.role.
-- profiles.role DB values: agent | compliance | marketing | admin (0001_identity.sql).
-- Global tags half (deterministic). The TIER half (admin->account_admin, others->member)
-- is per-instance and lives in the runbook (needs an instance_id profiles lacks).
-- Idempotent: on conflict do nothing so admin-API-set tags are never clobbered.

insert into public.user_tags (user_id, tag)
  select user_id, 'compliance' from public.profiles where role = 'compliance'
  on conflict (user_id, tag) do nothing;

insert into public.user_tags (user_id, tag)
  select user_id, 'marketing' from public.profiles where role = 'marketing'
  on conflict (user_id, tag) do nothing;
```

- [ ] **Step 2: Commit**

```bash
cd /d/workspaces/jnow-site
git add development/core/supabase/migrations/0014_migrate_profiles_tags.sql
git commit -m "feat(core): migrate profiles.role compliance/marketing -> global user_tags (2a.3)"
```

---

### Task 3: `roles.py` — global tag store + cached resolution

**Files:**
- Modify: `D:\devprojects\ollie-hermes-orchestrator\src\api\roles.py`
- Test: `D:\devprojects\ollie-hermes-orchestrator\tests\test_roles.py`

**Interfaces:**
- Consumes: `user_tags` (Task 1); env `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`.
- Produces (used by Task 4): `list_user_tags(user_id: str) -> list[str]` (GLOBAL, cached, fail-closed to `[]`); `set_user_tags(user_id: str, tags: list[str]) -> None`; `invalidate_tags(user_id: str | None = None) -> None`. Mirrors the existing tier cache/store pattern in the same file.

- [ ] **Step 1: Write the failing tests** (add to `tests/test_roles.py`; the file already has `_env` fixture with SUPABASE_URL/KEY + `roles.invalidate_cache()`)

```python
def test_list_user_tags_reads_and_caches(monkeypatch):
    calls = []
    monkeypatch.setattr(roles, "_fetch_tags", lambda uid: calls.append(1) or ["compliance"])
    roles.invalidate_tags()
    assert roles.list_user_tags("u-1") == ["compliance"]
    assert roles.list_user_tags("u-1") == ["compliance"]  # cached
    assert len(calls) == 1
    roles.invalidate_tags("u-1")
    roles.list_user_tags("u-1")
    assert len(calls) == 2


def test_list_user_tags_fails_closed_to_empty(monkeypatch):
    def boom(uid):
        raise RuntimeError("down")
    monkeypatch.setattr(roles, "_fetch_tags", boom)
    roles.invalidate_tags()
    assert roles.list_user_tags("u-1") == []
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_roles.py -k user_tags -v` → FAIL (no `list_user_tags`).

- [ ] **Step 3: Implement** in `src/api/roles.py` (add after the tier cache/helpers; reuse `_sb`, `_sb_headers`, `_now_iso`, `time`):

```python
_tags_cache: dict[str, tuple[list[str], float]] = {}  # user_id -> (tags, expiry)


def _fetch_tags(user_id: str) -> list[str]:
    sb = _sb()
    if not sb:
        return []
    url, key = sb
    resp = httpx.get(
        f"{url}/rest/v1/user_tags",
        params={"user_id": f"eq.{user_id}", "select": "tag"},
        headers=_sb_headers(key), timeout=10.0,
    )
    resp.raise_for_status()
    return sorted(r["tag"] for r in resp.json())


def list_user_tags(user_id: str) -> list[str]:
    """GLOBAL functional tags for a user; cached; fail-closed to [] on any error."""
    now = time.monotonic()
    hit = _tags_cache.get(user_id)
    if hit and hit[1] > now:
        return hit[0]
    try:
        tags = _fetch_tags(user_id)
    except Exception:
        _logger.warning("list_user_tags failed; defaulting []", exc_info=True)
        tags = []
    _tags_cache[user_id] = (tags, now + _CACHE_TTL)
    return tags


def invalidate_tags(user_id: str | None = None) -> None:
    if user_id is None:
        _tags_cache.clear()
    else:
        _tags_cache.pop(user_id, None)


def set_user_tags(user_id: str, tags: list[str]) -> None:
    """Replace a user's global tags (delete-all + insert). Service role."""
    sb = _sb()
    if not sb:
        raise RuntimeError("Supabase not configured")
    url, key = sb
    # Delete existing, then insert the new set (small sets; simplest correct).
    httpx.delete(
        f"{url}/rest/v1/user_tags",
        params={"user_id": f"eq.{user_id}"},
        headers=_sb_headers(key), timeout=10.0,
    ).raise_for_status()
    clean = [t for t in {str(x).strip() for x in tags} if t]
    if clean:
        httpx.post(
            f"{url}/rest/v1/user_tags",
            headers={**_sb_headers(key), "Prefer": "return=minimal"},
            json=[{"user_id": user_id, "tag": t} for t in clean],
            timeout=10.0,
        ).raise_for_status()
    invalidate_tags(user_id)
```

- [ ] **Step 4: Run** — `python -m pytest tests/test_roles.py -v` → PASS. `python -m pytest tests/ src/ -q` → no new failures.

- [ ] **Step 5: Commit**

```bash
git add src/api/roles.py tests/test_roles.py
git commit -m "feat(roles): global user_tags store + cached fail-closed resolution (2a.3)"
```

---

### Task 4: whoami + admin API expose/manage tags

**Files:**
- Modify: `D:\devprojects\ollie-hermes-orchestrator\src\api\admin.py`
- Test: `D:\devprojects\ollie-hermes-orchestrator\tests\test_admin_api.py`

**Interfaces:**
- Consumes: `roles.list_user_tags`/`set_user_tags` (Task 3).
- Produces: `whoami` returns `tags`; `GET /v1/admin/users` rows include `tags`; `PUT /v1/admin/users/{user_id}/tags` (account_admin+, body `{"tags": [...]}`, governance-audited).

- [ ] **Step 1: Write the failing tests** (add to `tests/test_admin_api.py`)

```python
def test_whoami_includes_tags(client, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "member")
    monkeypatch.setattr(roles, "get_labels", lambda i: dict(roles.DEFAULT_LABELS))
    monkeypatch.setattr(roles, "list_user_tags", lambda u: ["compliance"])
    r = client.get("/v1/whoami", headers={"X-Auth-User-Id": MEMBER})
    assert r.status_code == 200
    assert r.json()["tags"] == ["compliance"]


def test_set_user_tags_admin_only_and_audits(client, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "account_admin")
    writes, events = [], []
    monkeypatch.setattr(roles, "set_user_tags", lambda uid, tags: writes.append((uid, tags)))
    monkeypatch.setattr(admin, "_emit_admin_event", lambda *a, **k: events.append(a))
    r = client.put(f"/v1/admin/users/{MEMBER}/tags", json={"tags": ["compliance"]},
                   headers={"X-Auth-User-Id": ADMIN})
    assert r.status_code == 200
    assert writes == [(MEMBER, ["compliance"])]
    assert len(events) == 1


def test_set_user_tags_forbidden_for_member(client, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "member")
    r = client.put(f"/v1/admin/users/{MEMBER}/tags", json={"tags": ["x"]},
                   headers={"X-Auth-User-Id": MEMBER})
    assert r.status_code == 403
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_admin_api.py -k tags -v` → FAIL.

- [ ] **Step 3: Implement** in `src/api/admin.py`:

In `whoami` (after `label = ...`), add tags to the returned dict:
```python
    return {"userId": uid, "tier": tier, "label": label,
            "tags": roles.list_user_tags(uid),
            "reachableAgentIds": authz.reachable_agent_ids(request, cfg)}
```
In `admin_users`, add each user's tags to the row dict:
```python
        out.append({"userId": uid, "email": email, "tier": tier,
                    "label": labels.get(tier, tier), "tags": roles.list_user_tags(uid)})
```
Add the route (near `set_user_role`):
```python
class TagsBody(BaseModel):
    tags: list[str]


@router.put("/v1/admin/users/{user_id}/tags")
def set_user_tags_route(user_id: str, body: TagsBody, request: Request):
    caller, deny = _require_admin(request)
    if deny:
        return deny
    roles.set_user_tags(user_id, body.tags)
    _emit_admin_event(request, "tags.set", user_id, ",".join(body.tags), caller[0], caller[1])
    return {"userId": user_id, "tags": body.tags}
```
(Note `_emit_admin_event` gained an `actor_tier` param in the 2a final fixes — pass `caller[1]`. Confirm the signature before wiring.)

- [ ] **Step 4: Run** — `python -m pytest tests/test_admin_api.py -v` → PASS. `python -m pytest tests/ src/ -q` → no new failures.

- [ ] **Step 5: Commit**

```bash
git add src/api/admin.py tests/test_admin_api.py
git commit -m "feat(admin): whoami returns tags + PUT /v1/admin/users/{id}/tags (2a.3)"
```

---

### Task 5: Auth hook — additively emit `tags` (Supabase, login-critical)

**Files:**
- Create: `D:\workspaces\jnow-site\development\core\supabase\migrations\0015_hook_emit_tags.sql`

**Interfaces:**
- Consumes: `user_tags` (Task 1). Produces: the session JWT gains a `tags` array claim; `user_role` is UNCHANGED (still emitted).

- [ ] **Step 1: Write the migration** — replace the hook function additively (keeps the `user_role` logic verbatim from `0001`, adds tags; backward-tolerant to no rows)

```sql
-- 0015_hook_emit_tags.sql — access-token hook ALSO stamps global `tags`.
-- ADDITIVE: user_role emission is unchanged (governance step-2 cutover comes later,
-- 0016). A broken hook blocks logins, so this is deployed + verified BEFORE 0016.

create or replace function public.custom_access_token_hook(event jsonb)
returns jsonb
language plpgsql
as $$
declare
  claims jsonb;
  found_role text;
  found_tags jsonb;
begin
  select role into found_role from public.profiles where user_id = (event->>'user_id')::uuid;
  select coalesce(jsonb_agg(tag), '[]'::jsonb) into found_tags
    from public.user_tags where user_id = (event->>'user_id')::uuid;
  claims := event->'claims';
  claims := jsonb_set(claims, '{user_role}', to_jsonb(coalesce(found_role, 'agent')));
  claims := jsonb_set(claims, '{tags}', coalesce(found_tags, '[]'::jsonb));
  event := jsonb_set(event, '{claims}', claims);
  return event;
end;
$$;

grant execute on function public.custom_access_token_hook to supabase_auth_admin;
revoke execute on function public.custom_access_token_hook from authenticated, anon, public;
```

- [ ] **Step 2: Commit** (application + login verification is a runbook GATE — Task 8)

```bash
cd /d/workspaces/jnow-site
git add development/core/supabase/migrations/0015_hook_emit_tags.sql
git commit -m "feat(core): access-token hook additively emits global tags claim (2a.3, login-critical)"
```

---

### Task 6: Governance RLS cutover to tags (Supabase, staged after hook)

**Files:**
- Create: `D:\workspaces\jnow-site\development\core\supabase\migrations\0016_governance_rls_tags.sql`

**Interfaces:**
- Consumes: the `tags` claim (Task 5, must be verified live first). Produces: `governance_events` broad-read = compliance-tagged OR global `admin` role OR own email.

- [ ] **Step 1: Write the migration** (drop + recreate the 0005 select policy)

```sql
-- 0016_governance_rls_tags.sql — governance broad-read on tags, not the old user_role list.
-- STAGED: apply ONLY after 0015 is live and the `tags` claim is verified on real logins.
-- Broad read = compliance-tagged OR the global `admin` functional role; both global,
-- matching the shared/instance-blind governance_events table. Per-instance account_admin
-- is deliberately NOT a governance grant.

drop policy if exists governance_events_select on public.governance_events;

create policy governance_events_select on public.governance_events
  for select to authenticated
  using (
    (auth.jwt() -> 'tags') ? 'compliance'
    or coalesce(auth.jwt() ->> 'user_role', '') = 'admin'
    or user_email = coalesce(auth.jwt() ->> 'email', '')
  );
```

- [ ] **Step 2: Commit**

```bash
cd /d/workspaces/jnow-site
git add development/core/supabase/migrations/0016_governance_rls_tags.sql
git commit -m "feat(core): governance_events broad-read via compliance tag + admin role (2a.3)"
```

---

### Task 7: Frontend — one `useIdentity` hook + nav/route migration

**Files:**
- Create: `D:\devprojects\ollie-hermes-frontend\src\hooks\useIdentity.ts`
- Modify: `D:\devprojects\ollie-hermes-frontend\src\components\Layout.tsx` (nav item type + gating)
- Modify: `D:\devprojects\ollie-hermes-frontend\src\components\RoleRoute.tsx`, `src\App.tsx` (CapabilityRoute), and any `useUserRole` consumers
- Delete/retire: `src\hooks\useUserRole.ts` (+ its `Role` type usages)
- Test: `src\hooks\__tests__\useIdentity.test.ts` (+ update `Layout.test.tsx`, `RoleRoute`/consumers' tests)

**Interfaces:**
- Consumes: `GET /orchestrator-proxy/v1/whoami` → `{ userId, tier, label, tags, reachableAgentIds }` (Task 4).
- Produces: `useIdentity() -> { tier: Tier | null; tags: string[]; reachableAgentIds: string[]; loading: boolean }` where `Tier = 'member'|'manager'|'account_admin'|'platform_operator'`; a helper `atLeast(tier, min): boolean`. Nav items gate on `minTier?`/`anyTag?`.

- [ ] **Step 1: Write the failing `useIdentity` test** (follow the `useUserRole` test pattern — module cache + a mocked fetch)

```typescript
import { renderHook, waitFor } from '@testing-library/react';
import { useIdentity, atLeast, __resetIdentityCache } from '../useIdentity';

beforeEach(() => __resetIdentityCache());

it('loads tier + tags from whoami', async () => {
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: true, json: async () => ({ tier: 'member', tags: ['compliance'], reachableAgentIds: ['default'] }),
  }) as unknown as typeof fetch;
  const { result } = renderHook(() => useIdentity());
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(result.current.tier).toBe('member');
  expect(result.current.tags).toEqual(['compliance']);
});

it('atLeast ranks tiers', () => {
  expect(atLeast('account_admin', 'manager')).toBe(true);
  expect(atLeast('member', 'account_admin')).toBe(false);
});

it('fails safe (null tier, empty tags) when whoami errors', async () => {
  globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, status: 401 }) as unknown as typeof fetch;
  const { result } = renderHook(() => useIdentity());
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(result.current.tier).toBeNull();
  expect(result.current.tags).toEqual([]);
});
```

- [ ] **Step 2: Run to verify failure** — `npx vitest run src/hooks/__tests__/useIdentity.test.ts` → FAIL (module missing).

- [ ] **Step 3: Implement `src/hooks/useIdentity.ts`**

```typescript
import { useEffect, useState } from 'react';

export type Tier = 'member' | 'manager' | 'account_admin' | 'platform_operator';
const RANK: Record<Tier, number> = { member: 0, manager: 1, account_admin: 2, platform_operator: 3 };

export function atLeast(tier: Tier | null, min: Tier): boolean {
  return tier != null && RANK[tier] >= RANK[min];
}

export interface Identity {
  tier: Tier | null;
  tags: string[];
  reachableAgentIds: string[];
  loading: boolean;
}

let _cache: Promise<Omit<Identity, 'loading'>> | null = null;

async function load(): Promise<Omit<Identity, 'loading'>> {
  try {
    const res = await fetch('/orchestrator-proxy/v1/whoami');
    if (!res.ok) return { tier: null, tags: [], reachableAgentIds: [] };
    const d = await res.json();
    return {
      tier: (d.tier ?? null) as Tier | null,
      tags: Array.isArray(d.tags) ? d.tags : [],
      reachableAgentIds: Array.isArray(d.reachableAgentIds) ? d.reachableAgentIds : [],
    };
  } catch {
    return { tier: null, tags: [], reachableAgentIds: [] };
  }
}

export function useIdentity(): Identity {
  const [state, setState] = useState<Identity>({ tier: null, tags: [], reachableAgentIds: [], loading: true });
  useEffect(() => {
    let cancelled = false;
    if (!_cache) _cache = load();
    _cache.then(v => { if (!cancelled) setState({ ...v, loading: false }); });
    return () => { cancelled = true; };
  }, []);
  return state;
}

export function __resetIdentityCache(): void { _cache = null; }
```

- [ ] **Step 4: Migrate the nav + routes.** In `Layout.tsx`: replace the nav item `roles?: Role[]` field with `minTier?: Tier` and `anyTag?: string[]`; the visibility check becomes
  `(!item.minTier || atLeast(id.tier, item.minTier)) && (!item.anyTag || item.anyTag.some(t => id.tags.includes(t)))`
  using `const id = useIdentity()`. Set the management nav items (`skills, schedules, settings, logs, env, usage, models, profiles, plugins, memory_providers`) to `minTier: 'account_admin'`; set Compliance/Verification/TRAIGA to `{ minTier: 'account_admin', anyTag: ['compliance'] }` (visible to admins OR compliance-tagged — the check ORs minTier and anyTag: adjust the predicate so an item with BOTH is visible when EITHER matches). Migrate `RoleRoute.tsx` + `App.tsx`'s `CapabilityRoute` and any other `useUserRole` importer to `useIdentity`/`atLeast`. Delete `useUserRole.ts` and remove the `Role` type. Keep `useCapability` (separate feature-flag axis).

- [ ] **Step 5: Update the affected tests** (`Layout.test.tsx`, `RoleRoute` test, any `useUserRole` mock) to mock `useIdentity`/whoami instead of `profiles.role`. Then full `npx vitest run` (bash shell) → all pass; `npx tsc --noEmit` → clean.

- [ ] **Step 6: Commit**

```bash
git add src/hooks/useIdentity.ts src/components/Layout.tsx src/components/RoleRoute.tsx src/App.tsx src/hooks/__tests__/useIdentity.test.ts
git rm src/hooks/useUserRole.ts
git commit -m "feat(identity): single useIdentity hook (tier+tags); retire useUserRole/Role (2a.3)"
```

---

### Task 8: Staged rollout runbook

**Files:**
- Create: `D:\devprojects\ollie-hermes-orchestrator\docs\runbooks\identity-consolidation-2a3-rollout.md`

**Interfaces:** Documentation. No code/tests.

- [ ] **Step 1: Write the runbook** — SANDBOX first, each stage independently reversible:

1. **Apply `0013` (user_tags) + `0014` (tag migration)** to Supabase (`kpdqhntsvjzhqjeupzsj`) SQL editor.
2. **Seed tiers per instance (parameterized — the migration's tier half):** for each instance, from `profiles.role`:
   ```sql
   insert into public.user_roles (instance_id, user_id, tier)
     select 'sandbox', user_id, case when role='admin' then 'account_admin' else 'member' end
     from public.profiles
   on conflict (instance_id, user_id) do nothing;
   ```
   (repeat with `'jnow'` for the jnow box; `on conflict do nothing` preserves admin-API-set tiers, incl. John's seeded `platform_operator`).
3. **Apply `0015` (hook additive) — LOGIN GATE:** apply, then IMMEDIATELY verify: log into `olliesandbox.jnow.io`; confirm login succeeds and a fresh token carries `tags` (decode the JWT or check via a whoami/curl). If login breaks, roll back `0015` by re-applying `0001`'s hook body. Do NOT proceed until logins are proven.
4. **Deploy orchestrator + frontend** (whoami tags + `useIdentity`): pull master on the box, restart orchestrator; rebuild the frontend image (tag `:rbac-phase2a` line — new tag, don't clobber prod-shared). Verify `whoami` returns `tags`; the management nav hides for a member; a compliance-tagged member sees Compliance but not management.
5. **Apply `0016` (governance RLS cutover)** — only after step 3+4 verified. Verify: a compliance-tagged (or `admin`) user reads all governance events; a plain member reads only their own.
6. **jnow** — repeat 1–5 only after sandbox is fully verified.
7. **Rollback per stage:** `0016` → re-apply `0005`'s policy; `0015` → re-apply `0001`'s hook; orchestrator/frontend → retag image + `git checkout` + restart; tables/tags are additive and inert without the code.

- [ ] **Step 2: Commit**

```bash
git add docs/runbooks/identity-consolidation-2a3-rollout.md
git commit -m "docs: staged rollout runbook for identity consolidation (2a.3)"
```

---

## Self-Review Notes

- **Spec coverage:** user_tags global = Task 1; profiles.role migration = Task 2 (tags) + Task 8 step 2 (tiers, parameterized); roles.py tags = Task 3; whoami+admin tags = Task 4; additive hook = Task 5; governance RLS = Task 6; useIdentity + retire useUserRole = Task 7; staged rollout = Task 8. Tier-never-in-JWT = respected (no task stamps tier). 
- **Login-critical staging:** Tasks 5 (hook) and 6 (RLS) are separate migrations applied in separate, verified runbook stages; the hook is additive (keeps `user_role`); logins are a hard gate before RLS cutover. Retiring `user_role` is deferred (kept — 0016 still reads `user_role='admin'`).
- **Known v1 boundaries:** tags admin UI is 2b (only the API here); `set_user_tags` is delete-all-then-insert (fine for tiny tag sets); the tier-migration half is a parameterized runbook SQL, not an auto-migration, because `profiles` has no instance_id.
- **Type consistency:** `list_user_tags(user_id)`/`set_user_tags(user_id, tags)`/`invalidate_tags` (Task 3) match Task 4 usage; whoami `tags` shape matches Task 7's `useIdentity`; `Tier` union + `atLeast` consistent in Task 7; `_emit_admin_event` actor_tier arg matches the 2a final-fix signature (Task 4 flags a confirm).
