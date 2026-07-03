# Phase 2a — RBAC + Scope Taxonomy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the orchestrator a real role model (member/manager/account_admin/platform_operator) backed by a Supabase table, and gate agent access by role × agent-scope, fail-closed, with a `/v1/whoami` for the frontend picker and an admin API for the future UIs.

**Architecture:** Spec: `docs/superpowers/specs/2026-07-03-rbac-scope-taxonomy-phase2a-design.md`. A new `user_roles` table (instance-scoped, shared Supabase project) is the source of truth; `src/api/roles.py` resolves a caller's tier by `user_id` with a short-TTL cache. Agents gain `scope: user|company` + `manager_visible` in `AGENTS_JSON`; `src/api/authz.py` combines tier + agent scope into a fail-closed `403` applied on the run-proxy and session endpoints, layered beside the Phase 1 ownership gate. `src/api/admin.py` adds `/v1/whoami` + `/v1/admin/*`. The frontend reads `whoami` and renders only reachable agents.

**Tech Stack:** Python 3.11 + FastAPI + httpx + pytest (orchestrator); TypeScript + React + vitest (frontend); Supabase Postgres (migration SQL).

## Global Constraints

- **Fail-closed everywhere:** a user with no `user_roles` row resolves to `member`; any store/lookup error resolves to `member`; a denied agent-access check returns `403 {"detail": "Forbidden"}` **before** Hermes/gateway is touched. (Spec §2, §4, §6.)
- **Canonical tiers are fixed and ordered:** `member` < `manager` < `account_admin` < `platform_operator`. Labels are cosmetic and never affect an enforcement decision. (Spec §2.)
- **`instance_id` is required, not cosmetic** — sandbox and jnow share one Supabase project (`kpdqhntsvjzhqjeupzsj`); the orchestrator reads its own id from `INSTANCE_ID` env (default `"default"`) and scopes every `user_roles`/`role_labels` query by it. (Spec §3.)
- **The JWT `X-Auth-Role` claim is no longer trusted for authorization** — `user_roles` is authoritative. `X-Auth-User-Id` (Phase 1) identifies the caller. (Spec §4.)
- **Identity-less bearer callers** (valid `ORCHESTRATOR_KEY`, no `X-Auth-User-Id`) are internal/trusted and skip role/access checks — keeps existing suites green. (Spec §6.)
- **Hermes is never modified.** Supabase writes use the service-role key via direct httpx PostgREST calls (the `_write_event` pattern in `runs.py`). No new dependencies.
- **Agent scope default is `company`** (unmarked agents stay admin-gated); Ollie/`default` is explicitly `user`. `manager_visible` defaults `false`. (Spec §5.)
- Orchestrator test command (Windows): `python -m pytest tests/ src/ -q` — a KNOWN pre-existing set of 17 Windows failures (config-path + pytest-asyncio) is acceptable; anything beyond that is a regression. Frontend: `npx vitest run` (from a bash/sh-available shell — the generate-*.sh tests need `sh` on PATH) + `npx tsc --noEmit`.
- Repos: orchestrator on `master` at `D:\devprojects\ollie-hermes-orchestrator`; frontend on `master` at `D:\devprojects\ollie-hermes-frontend`; the migration in the jnow-site worktree `D:\workspaces\jnow-site` (holds jnow-workspace `main` — NEVER `git switch main` in `D:\workspaces\jnow-workspace`). Migration number is **0012** (0010 = traiga readiness, 0011 = agent_sessions).
- Commit after each task; end commit messages with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: `user_roles` + `role_labels` migration (Supabase)

**Files:**
- Create: `D:\workspaces\jnow-site\development\core\supabase\migrations\0012_user_roles.sql`

**Interfaces:**
- Produces: `public.user_roles` (PK `(instance_id, user_id)`, `tier` checked) and
  `public.role_labels` (PK `(instance_id, tier)`), consumed via PostgREST by Tasks 3, 7.

- [ ] **Step 1: Write the migration** (mirror `0011_agent_sessions.sql` style)

```sql
-- 0012_user_roles.sql — RBAC roles + per-instance role labels
-- (agent instantiation Phase 2a; spec: ollie-hermes-orchestrator
-- docs/superpowers/specs/2026-07-03-rbac-scope-taxonomy-phase2a-design.md).

create table if not exists public.user_roles (
  instance_id  text not null,
  user_id      uuid not null,
  tier         text not null
    check (tier in ('member','manager','account_admin','platform_operator')),
  assigned_by  uuid,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  primary key (instance_id, user_id)
);

alter table public.user_roles enable row level security;

-- SELECT: a user may read only their own row (defense in depth; the orchestrator
-- reads via the service role, which bypasses RLS). No write policies -> only the
-- service role (the orchestrator admin API) may write.
create policy user_roles_select_own on public.user_roles
  for select to authenticated
  using (user_id = auth.uid());

create table if not exists public.role_labels (
  instance_id  text not null,
  tier         text not null
    check (tier in ('member','manager','account_admin','platform_operator')),
  label        text not null,
  primary key (instance_id, tier)
);

alter table public.role_labels enable row level security;

-- Labels are non-sensitive display text; any authenticated user may read them.
create policy role_labels_select_all on public.role_labels
  for select to authenticated using (true);

comment on table public.user_roles is
  'Phase 2a: instance-scoped RBAC tier per user; source of truth, orchestrator resolves by user_id.';
comment on table public.role_labels is
  'Phase 2a: per-instance customizable display labels for canonical tiers (cosmetic; never affects enforcement).';
```

- [ ] **Step 2: Verify worktree + commit**

```bash
cd /d/workspaces/jnow-site
git branch --show-current   # expect: main; if not, STOP
git add development/core/supabase/migrations/0012_user_roles.sql
git commit -m "feat(core): user_roles + role_labels tables (RBAC Phase 2a)"
```

Applying to the live Supabase project is a deploy step (runbook, Task 9), not this task.

---

### Task 2: `INSTANCE_ID` in Config

**Files:**
- Modify: `D:\devprojects\ollie-hermes-orchestrator\src\config.py`
- Test: `D:\devprojects\ollie-hermes-orchestrator\tests\test_config.py`

**Interfaces:**
- Produces: `Config.instance_id: str` (from `INSTANCE_ID` env, default `"default"`), consumed by Tasks 3, 7 (via `request.app.state.config`).

- [ ] **Step 1: Write the failing test** — add to `tests/test_config.py` (follow its existing env-set/monkeypatch style; `test_config` has a known Windows path failure, but this new assertion is env-only):

```python
def test_instance_id_defaults_and_reads_env(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_KEY", "k")
    monkeypatch.delenv("INSTANCE_ID", raising=False)
    from src.config import Config
    assert Config.load().instance_id == "default"
    monkeypatch.setenv("INSTANCE_ID", "sandbox")
    assert Config.load().instance_id == "sandbox"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_config.py::test_instance_id_defaults_and_reads_env -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'instance_id'`.

- [ ] **Step 3: Implement** — add the field + load line in `src/config.py`:

In the `Config` dataclass (after `audit_log_path: Path`):
```python
    instance_id: str
```
In `load()`, before the `return cls(`:
```python
        instance_id = os.environ.get("INSTANCE_ID", "").strip() or "default"
```
And add `instance_id=instance_id,` to the `cls(...)` call.

- [ ] **Step 4: Run** — `python -m pytest tests/test_config.py::test_instance_id_defaults_and_reads_env -v` → PASS. Then `python -m pytest tests/ src/ -q` → no NEW failures beyond the known baseline.

- [ ] **Step 5: Commit**

```bash
git add src/config.py tests/test_config.py
git commit -m "feat(config): INSTANCE_ID for instance-scoped RBAC lookups"
```

---

### Task 3: `roles.py` — tier model + store + resolution

**Files:**
- Create: `D:\devprojects\ollie-hermes-orchestrator\src\api\roles.py`
- Create: `D:\devprojects\ollie-hermes-orchestrator\tests\test_roles.py`

**Interfaces:**
- Consumes: `user_roles`/`role_labels` (Task 1); env `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`.
- Produces (used by Tasks 5, 7):
  - `TIERS: tuple[str,...]` = `("member","manager","account_admin","platform_operator")`
  - `DEFAULT_LABELS: dict[str,str]`
  - `resolve_tier(instance_id: str, user_id: str) -> str` — cached, fail-closed to `"member"`
  - `is_at_least(tier: str, minimum: str) -> bool`
  - `set_tier(instance_id, user_id, tier, assigned_by) -> None`
  - `list_roles(instance_id) -> dict[user_id, tier]`
  - `get_labels(instance_id) -> dict[str,str]` (defaults merged)
  - `set_labels(instance_id, labels: dict[str,str]) -> None`
  - `invalidate_cache(user_id: str | None = None) -> None`

- [ ] **Step 1: Write the failing tests**

```python
"""RBAC tier model, store, and resolution (Phase 2a). Supabase I/O monkeypatched."""
import pytest
import src.api.roles as roles

INST = "sandbox"
U = "aaaaaaaa-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    roles.invalidate_cache()
    yield
    roles.invalidate_cache()


def test_tiers_and_ordering():
    assert roles.TIERS == ("member", "manager", "account_admin", "platform_operator")
    assert roles.is_at_least("account_admin", "manager") is True
    assert roles.is_at_least("member", "manager") is False
    assert roles.is_at_least("platform_operator", "platform_operator") is True


def test_resolve_tier_reads_row(monkeypatch):
    monkeypatch.setattr(roles, "_fetch_tier", lambda inst, uid: "manager")
    assert roles.resolve_tier(INST, U) == "manager"


def test_resolve_tier_defaults_member_when_absent(monkeypatch):
    monkeypatch.setattr(roles, "_fetch_tier", lambda inst, uid: None)
    assert roles.resolve_tier(INST, U) == "member"


def test_resolve_tier_fails_closed_on_error(monkeypatch):
    def boom(inst, uid):
        raise RuntimeError("supabase down")
    monkeypatch.setattr(roles, "_fetch_tier", boom)
    assert roles.resolve_tier(INST, U) == "member"


def test_resolve_tier_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(roles, "_fetch_tier", lambda inst, uid: calls.append(1) or "manager")
    roles.resolve_tier(INST, U)
    roles.resolve_tier(INST, U)
    assert len(calls) == 1  # second call served from cache
    roles.invalidate_cache(U)
    roles.resolve_tier(INST, U)
    assert len(calls) == 2


def test_get_labels_merges_defaults(monkeypatch):
    monkeypatch.setattr(roles, "_fetch_labels", lambda inst: {"manager": "Team Lead"})
    labels = roles.get_labels(INST)
    assert labels["manager"] == "Team Lead"
    assert labels["member"] == roles.DEFAULT_LABELS["member"]
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_roles.py -v` → FAIL (`No module named 'src.api.roles'`).

- [ ] **Step 3: Implement `src/api/roles.py`**

```python
"""RBAC tier model + Supabase-backed store + cached resolution (Phase 2a).

Canonical tiers are fixed and ordered; labels are cosmetic. `user_roles` is the
source of truth (instance-scoped). resolve_tier() is fail-closed to 'member'.
Writes use the service role via PostgREST (the _write_event pattern in runs.py).
"""
import logging
import os
import time

import httpx

_logger = logging.getLogger(__name__)

TIERS: tuple[str, ...] = ("member", "manager", "account_admin", "platform_operator")
_RANK = {t: i for i, t in enumerate(TIERS)}
DEFAULT_LABELS: dict[str, str] = {
    "member": "Member",
    "manager": "Manager",
    "account_admin": "Account Admin",
    "platform_operator": "JNOW Operator",
}

_CACHE_TTL = 30.0  # seconds
# user_id -> (tier, monotonic_expiry)
_tier_cache: dict[str, tuple[str, float]] = {}


def is_at_least(tier: str, minimum: str) -> bool:
    return _RANK.get(tier, -1) >= _RANK.get(minimum, len(TIERS))


def _sb() -> tuple[str, str] | None:
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    return (url, key) if url and key else None


def _sb_headers(key: str) -> dict:
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _fetch_tier(instance_id: str, user_id: str) -> str | None:
    sb = _sb()
    if not sb:
        return None
    url, key = sb
    resp = httpx.get(
        f"{url}/rest/v1/user_roles",
        params={"instance_id": f"eq.{instance_id}", "user_id": f"eq.{user_id}", "select": "tier"},
        headers=_sb_headers(key), timeout=10.0,
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0]["tier"] if rows else None


def resolve_tier(instance_id: str, user_id: str) -> str:
    """Caller's tier, cached; fail-closed to 'member' on absence or any error."""
    now = time.monotonic()
    hit = _tier_cache.get(user_id)
    if hit and hit[1] > now:
        return hit[0]
    try:
        tier = _fetch_tier(instance_id, user_id) or "member"
        if tier not in _RANK:
            tier = "member"
    except Exception:
        _logger.warning("resolve_tier failed; defaulting member", exc_info=True)
        tier = "member"
    _tier_cache[user_id] = (tier, now + _CACHE_TTL)
    return tier


def invalidate_cache(user_id: str | None = None) -> None:
    if user_id is None:
        _tier_cache.clear()
    else:
        _tier_cache.pop(user_id, None)


def set_tier(instance_id: str, user_id: str, tier: str, assigned_by: str | None) -> None:
    if tier not in _RANK:
        raise ValueError(f"invalid tier: {tier}")
    sb = _sb()
    if not sb:
        raise RuntimeError("Supabase not configured")
    url, key = sb
    httpx.post(
        f"{url}/rest/v1/user_roles",
        params={"on_conflict": "instance_id,user_id"},
        headers={**_sb_headers(key), "Prefer": "resolution=merge-duplicates,return=minimal"},
        json={"instance_id": instance_id, "user_id": user_id, "tier": tier,
              "assigned_by": assigned_by, "updated_at": _now_iso()},
        timeout=10.0,
    ).raise_for_status()
    invalidate_cache(user_id)


def list_roles(instance_id: str) -> dict[str, str]:
    sb = _sb()
    if not sb:
        return {}
    url, key = sb
    resp = httpx.get(
        f"{url}/rest/v1/user_roles",
        params={"instance_id": f"eq.{instance_id}", "select": "user_id,tier"},
        headers=_sb_headers(key), timeout=10.0,
    )
    resp.raise_for_status()
    return {r["user_id"]: r["tier"] for r in resp.json()}


def _fetch_labels(instance_id: str) -> dict[str, str]:
    sb = _sb()
    if not sb:
        return {}
    url, key = sb
    resp = httpx.get(
        f"{url}/rest/v1/role_labels",
        params={"instance_id": f"eq.{instance_id}", "select": "tier,label"},
        headers=_sb_headers(key), timeout=10.0,
    )
    resp.raise_for_status()
    return {r["tier"]: r["label"] for r in resp.json()}


def get_labels(instance_id: str) -> dict[str, str]:
    merged = dict(DEFAULT_LABELS)
    try:
        merged.update({t: l for t, l in _fetch_labels(instance_id).items() if t in _RANK})
    except Exception:
        _logger.warning("get_labels failed; using defaults", exc_info=True)
    return merged


def set_labels(instance_id: str, labels: dict[str, str]) -> None:
    sb = _sb()
    if not sb:
        raise RuntimeError("Supabase not configured")
    url, key = sb
    rows = [{"instance_id": instance_id, "tier": t, "label": str(l)[:60]}
            for t, l in labels.items() if t in _RANK]
    if not rows:
        return
    httpx.post(
        f"{url}/rest/v1/role_labels",
        params={"on_conflict": "instance_id,tier"},
        headers={**_sb_headers(key), "Prefer": "resolution=merge-duplicates,return=minimal"},
        json=rows, timeout=10.0,
    ).raise_for_status()


def _now_iso() -> str:
    # UTC ISO-8601; imported lazily so tests can monkeypatch time without import churn.
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
```

- [ ] **Step 4: Run** — `python -m pytest tests/test_roles.py -v` → ALL PASS. `python -m pytest tests/ src/ -q` → no new failures.

- [ ] **Step 5: Commit**

```bash
git add src/api/roles.py tests/test_roles.py
git commit -m "feat(roles): tier model + Supabase store + cached fail-closed resolution (Phase 2a)"
```

---

### Task 4: Agent scope taxonomy in `AGENTS_JSON`

**Files:**
- Modify: `D:\devprojects\ollie-hermes-orchestrator\src\agents_json.py`
- Test: `D:\devprojects\ollie-hermes-orchestrator\tests\test_agents_json.py`

**Interfaces:**
- Produces: `AgentEntry` gains `scope: str = "company"` and `manager_visible: bool = False`,
  round-tripped through `_json_to_entry` / `_entry_to_json`. Consumed by Task 5 (`read_agents`).

- [ ] **Step 1: Write the failing tests** — add to `tests/test_agents_json.py`:

```python
def test_entry_parses_scope_and_manager_visible():
    from src.agents_json import _json_to_entry
    e = _json_to_entry({"id": "default", "name": "Ollie",
                        "gatewayUrl": "http://h:8642", "dashboardUrl": "http://h:9119",
                        "scope": "user", "manager_visible": True})
    assert e.scope == "user"
    assert e.manager_visible is True


def test_entry_defaults_scope_company():
    from src.agents_json import _json_to_entry
    e = _json_to_entry({"id": "pam", "name": "Pam",
                        "gatewayUrl": "http://h:8643", "dashboardUrl": "http://h:9121"})
    assert e.scope == "company"
    assert e.manager_visible is False


def test_entry_roundtrips_scope():
    from src.agents_json import AgentEntry, _entry_to_json, _json_to_entry
    e = AgentEntry(id="pam", name="Pam", gateway_port=8643, dashboard_port=9121,
                   color="#888888", model=None, scope="company", manager_visible=True)
    out = _entry_to_json(e)
    assert out["scope"] == "company"
    assert out["manager_visible"] is True
    assert _json_to_entry(out).manager_visible is True
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_agents_json.py -k scope -v` → FAIL (`unexpected keyword argument 'scope'` / attribute error).

- [ ] **Step 3: Implement** in `src/agents_json.py`:

Add to the `AgentEntry` dataclass (after `model: Optional[str] = None`):
```python
    scope: str = "company"
    manager_visible: bool = False
```
In `_entry_to_json`, before `if e.model:`:
```python
    d["scope"] = e.scope
    d["manager_visible"] = e.manager_visible
```
In `_json_to_entry`, add to the `AgentEntry(...)` call:
```python
        scope=d.get("scope", "company"),
        manager_visible=bool(d.get("manager_visible", False)),
```

- [ ] **Step 4: Run** — `python -m pytest tests/test_agents_json.py -v` → PASS. `python -m pytest tests/ src/ -q` → no new failures.

- [ ] **Step 5: Commit**

```bash
git add src/agents_json.py tests/test_agents_json.py
git commit -m "feat(agents): scope + manager_visible on AGENTS_JSON entries (Phase 2a)"
```

---

### Task 5: `authz.py` — access decision + request helper

**Files:**
- Create: `D:\devprojects\ollie-hermes-orchestrator\src\api\authz.py`
- Create: `D:\devprojects\ollie-hermes-orchestrator\tests\test_authz.py`

**Interfaces:**
- Consumes: `roles.resolve_tier` / `is_at_least` (Task 3); `read_agents` + `AgentEntry.scope`/`manager_visible` (Task 4); `Config.instance_id` (Task 2).
- Produces (used by Tasks 6, 7):
  - `can_reach(tier: str, scope: str, manager_visible: bool) -> bool`
  - `agent_scope(agent_id: str, cfg) -> tuple[str, bool] | None` — `(scope, manager_visible)` or None if unknown
  - `check_agent_access(request, agent_id: str, cfg) -> JSONResponse | None` — `None` if allowed; a `403 {"detail":"Forbidden"}` response if denied. Identity-less callers → allowed (None).
  - `reachable_agent_ids(request, cfg) -> list[str]`

- [ ] **Step 1: Write the failing tests**

```python
"""Agent-access authorization (Phase 2a)."""
import types
import pytest
from fastapi import Request

import src.api.authz as authz
import src.api.roles as roles


def _req(user_id: str | None):
    headers = [(b"x-auth-user-id", user_id.encode())] if user_id else []
    scope = {"type": "http", "headers": headers}
    return Request(scope)


class _Entry:
    def __init__(self, id, scope, manager_visible=False):
        self.id = id
        self.scope = scope
        self.manager_visible = manager_visible


@pytest.fixture
def cfg(monkeypatch):
    c = types.SimpleNamespace(instance_id="sandbox", hermes_stack_dir=None)
    monkeypatch.setattr(authz, "read_agents", lambda _p: [
        _Entry("default", "user"),
        _Entry("pam", "company", manager_visible=False),
        _Entry("mkt", "company", manager_visible=True),
    ])
    # read_agents is called with cfg.hermes_stack_dir/'.env'; tolerate None path.
    monkeypatch.setattr(authz, "_env_path", lambda _cfg: "IGNORED")
    return c


def test_can_reach_matrix():
    assert authz.can_reach("member", "user", False) is True
    assert authz.can_reach("member", "company", False) is False
    assert authz.can_reach("manager", "company", False) is False
    assert authz.can_reach("manager", "company", True) is True
    assert authz.can_reach("account_admin", "company", False) is True
    assert authz.can_reach("platform_operator", "company", False) is True


def test_check_access_member_denied_company(cfg, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "member")
    r = authz.check_agent_access(_req("u1"), "pam", cfg)
    assert r is not None and r.status_code == 403


def test_check_access_member_allowed_user_agent(cfg, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "member")
    assert authz.check_agent_access(_req("u1"), "default", cfg) is None


def test_check_access_identity_less_allowed(cfg):
    assert authz.check_agent_access(_req(None), "pam", cfg) is None


def test_check_access_unknown_agent_denied(cfg, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "account_admin")
    r = authz.check_agent_access(_req("u1"), "nope", cfg)
    assert r is not None and r.status_code == 403


def test_reachable_ids_member(cfg, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "member")
    assert authz.reachable_agent_ids(_req("u1"), cfg) == ["default"]


def test_reachable_ids_manager(cfg, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "manager")
    assert set(authz.reachable_agent_ids(_req("u1"), cfg)) == {"default", "mkt"}
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_authz.py -v` → FAIL (`No module named 'src.api.authz'`).

- [ ] **Step 3: Implement `src/api/authz.py`**

```python
"""Agent-access authorization: tier (roles.py) x agent scope (agents_json).

Fail-closed. Identity-less callers (no X-Auth-User-Id) are internal/trusted and
are allowed — same trust boundary as the Phase 1 ownership gate.
"""
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

from src.agents_json import read_agents
from src.api import roles

_FORBIDDEN = JSONResponse({"detail": "Forbidden"}, status_code=403)


def can_reach(tier: str, scope: str, manager_visible: bool) -> bool:
    if scope == "user":
        return True  # any authenticated tier
    # scope == "company"
    if roles.is_at_least(tier, "account_admin"):
        return True
    if tier == "manager":
        return bool(manager_visible)
    return False


def _env_path(cfg) -> Path:
    return cfg.hermes_stack_dir / ".env"


def agent_scope(agent_id: str, cfg) -> tuple[str, bool] | None:
    for e in read_agents(_env_path(cfg)):
        if e.id == agent_id:
            return (e.scope, e.manager_visible)
    return None


def _user_id(request: Request) -> str:
    return request.headers.get("X-Auth-User-Id", "").strip()


def check_agent_access(request: Request, agent_id: str, cfg) -> JSONResponse | None:
    """None if allowed; a 403 response if denied. Identity-less -> allowed."""
    user_id = _user_id(request)
    if not user_id:
        return None  # trusted internal caller
    sc = agent_scope(agent_id, cfg)
    if sc is None:
        return _FORBIDDEN  # unknown agent — fail closed, don't leak existence
    scope, manager_visible = sc
    tier = roles.resolve_tier(cfg.instance_id, user_id)
    return None if can_reach(tier, scope, manager_visible) else _FORBIDDEN


def reachable_agent_ids(request: Request, cfg) -> list[str]:
    user_id = _user_id(request)
    entries = read_agents(_env_path(cfg))
    if not user_id:
        return [e.id for e in entries]
    tier = roles.resolve_tier(cfg.instance_id, user_id)
    return [e.id for e in entries if can_reach(tier, e.scope, e.manager_visible)]
```

- [ ] **Step 4: Run** — `python -m pytest tests/test_authz.py -v` → PASS. `python -m pytest tests/ src/ -q` → no new failures.

- [ ] **Step 5: Commit**

```bash
git add src/api/authz.py tests/test_authz.py
git commit -m "feat(authz): tier x agent-scope access decision + request helper (Phase 2a)"
```

---

### Task 6: Wire enforcement into run-proxy + session endpoints

**Files:**
- Modify: `D:\devprojects\ollie-hermes-orchestrator\src\api\runs.py`
- Modify: `D:\devprojects\ollie-hermes-orchestrator\src\api\sessions.py`
- Test: `D:\devprojects\ollie-hermes-orchestrator\tests\test_rbac_enforcement.py` (new)

**Interfaces:**
- Consumes: `authz.check_agent_access(request, agent, cfg)` (Task 5); the app config at `request.app.state.config`.
- Produces: `create_run`, `run_events`, `stop_run`, `approve_run`, `list_runs` (runs.py) and `list_sessions`, `session_messages`, `delete_session` (sessions.py) return `403 Forbidden` for a caller whose tier can't reach the agent — **before** the Phase 1 ownership gate and before any gateway/dashboard call.

**Note on config access:** these route modules currently read env directly, not `request.app.state.config`. Add a tiny local helper in each module:
```python
def _cfg(request):
    return request.app.state.config
```
The RBAC check is skipped gracefully if config is absent (e.g. isolated unit tests that mount only the router without app state) — guard with `getattr(request.app.state, "config", None)`; when None, skip the check (unit-test/trust path). Integration on the box always has config.

- [ ] **Step 1: Write the failing tests**

```python
"""RBAC enforcement on run-proxy + session endpoints (Phase 2a)."""
import json
import types
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.runs as runs
import src.api.sessions as sessions_mod
import src.api.authz as authz
from src.api.runs import router as runs_router
from src.api.sessions import router as sessions_router
from src.auth import require_bearer

MEMBER = "mmmmmmmm-0000-0000-0000-000000000001"


@pytest.fixture
def app_with_config(monkeypatch):
    monkeypatch.setenv("HERMES_GATEWAY_URL", "http://gw")
    monkeypatch.setenv("HERMES_GATEWAY_KEY", "k")
    monkeypatch.setenv("HERMES_DASHBOARD_URLS", json.dumps({"pam": "http://127.0.0.1:9121"}))
    app = FastAPI()
    app.state.config = types.SimpleNamespace(instance_id="sandbox", hermes_stack_dir=None)
    app.include_router(runs_router)
    app.include_router(sessions_router)
    app.dependency_overrides[require_bearer] = lambda: None
    # 'pam' is a company agent; force the caller's access check to deny.
    monkeypatch.setattr(authz, "check_agent_access",
                        lambda request, agent, cfg: authz._FORBIDDEN if agent == "pam" else None)
    return TestClient(app)


def test_member_blocked_from_company_agent_run(app_with_config):
    r = app_with_config.post("/v1/runs/pam", content=b'{"input":"hi"}',
                             headers={"X-Auth-User-Id": MEMBER})
    assert r.status_code == 403
    assert r.json() == {"detail": "Forbidden"}


def test_member_blocked_from_company_agent_sessions(app_with_config):
    r = app_with_config.get("/v1/sessions/pam", headers={"X-Auth-User-Id": MEMBER})
    assert r.status_code == 403


def test_allowed_agent_passes_rbac(app_with_config, monkeypatch):
    # 'default' is allowed by the stubbed check; run-proxy proceeds (gateway stubbed)
    monkeypatch.setattr(runs, "_create_run", lambda a, b: (200, b'{"run_id":"r1"}'))
    monkeypatch.setattr(runs, "screen_input", lambda inp, p: {"decision": "allow"})
    r = app_with_config.post("/v1/runs/default", content=b'{"input":"hi"}',
                             headers={"X-Auth-User-Id": MEMBER})
    assert r.status_code == 200
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_rbac_enforcement.py -v` → FAIL (endpoints return 200/other, RBAC not wired).

- [ ] **Step 3: Implement** — add the guard as the FIRST thing in each handler.

In `src/api/runs.py`, add near the top (after imports):
```python
from src.api import authz
```
Add a module helper:
```python
def _rbac_denied(request, agent):
    cfg = getattr(request.app.state, "config", None)
    if cfg is None:
        return None
    return authz.check_agent_access(request, agent, cfg)
```
Then, as the first statement inside `create_run`, `run_events`, `stop_run`, `approve_run`, and `list_runs` (each already takes `request: Request`):
```python
    denied = _rbac_denied(request, agent)
    if denied:
        return denied
```
(Place it before the existing `_gateway_base` check so a denied caller never learns whether the agent is configured.)

In `src/api/sessions.py`, add `from src.api import authz` and the same `_rbac_denied` helper, then insert the same two-line guard as the first statement of `list_sessions`, `session_messages`, and `delete_session` (before the Phase 1 identity/ownership checks).

- [ ] **Step 4: Run** — `python -m pytest tests/test_rbac_enforcement.py -v` → PASS. Then the Phase 1 suites must still pass: `python -m pytest tests/test_runs_ownership.py tests/test_runs_passthrough.py tests/test_sessions_api.py src/api/test_runs_guardrail.py -q` (these mount routers WITHOUT app.state.config, so `_rbac_denied` returns None and they're unaffected — verify). Full: `python -m pytest tests/ src/ -q` → no new failures.

- [ ] **Step 5: Commit**

```bash
git add src/api/runs.py src/api/sessions.py tests/test_rbac_enforcement.py
git commit -m "feat(rbac): fail-closed agent-access enforcement on run-proxy + sessions (Phase 2a)"
```

---

### Task 7: `/v1/whoami` + admin API

**Files:**
- Create: `D:\devprojects\ollie-hermes-orchestrator\src\api\admin.py`
- Modify: `D:\devprojects\ollie-hermes-orchestrator\src\api\main.py` (include router)
- Create: `D:\devprojects\ollie-hermes-orchestrator\tests\test_admin_api.py`

**Interfaces:**
- Consumes: `roles.*` (Task 3), `authz.reachable_agent_ids` (Task 5), `Config.instance_id` (Task 2).
- Produces:
  - `GET /v1/whoami` → `{userId, tier, label, reachableAgentIds}` (401 if no identity)
  - `GET /v1/admin/users` → `[{userId, email, tier, label}]` (account_admin+)
  - `PUT /v1/admin/users/{user_id}/role` body `{"tier": "..."}` (account_admin+; only platform_operator may assign platform_operator)
  - `GET /v1/admin/role-labels` → `{tier: label}`
  - `PUT /v1/admin/role-labels` body `{tier: label,...}` (account_admin+)
  - each admin write emits a governance event.

- [ ] **Step 1: Write the failing tests**

```python
"""whoami + admin API (Phase 2a). Supabase + role store monkeypatched."""
import json
import types
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.admin as admin
import src.api.roles as roles
import src.api.authz as authz
from src.api.admin import router as admin_router
from src.auth import require_bearer

ADMIN = "aaaaaaaa-0000-0000-0000-00000000000a"
MEMBER = "mmmmmmmm-0000-0000-0000-00000000000m"


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.state.config = types.SimpleNamespace(instance_id="sandbox", hermes_stack_dir=None)
    app.include_router(admin_router)
    app.dependency_overrides[require_bearer] = lambda: None
    monkeypatch.setattr(authz, "reachable_agent_ids", lambda request, cfg: ["default"])
    return TestClient(app)


def test_whoami_requires_identity(client):
    assert client.get("/v1/whoami").status_code == 401


def test_whoami_returns_tier_and_reachable(client, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "member")
    monkeypatch.setattr(roles, "get_labels", lambda i: dict(roles.DEFAULT_LABELS))
    r = client.get("/v1/whoami", headers={"X-Auth-User-Id": MEMBER})
    assert r.status_code == 200
    assert r.json() == {"userId": MEMBER, "tier": "member",
                        "label": "Member", "reachableAgentIds": ["default"]}


def test_admin_users_requires_admin(client, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "member")
    assert client.get("/v1/admin/users", headers={"X-Auth-User-Id": MEMBER}).status_code == 403


def test_admin_set_role_writes_and_audits(client, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "account_admin")
    writes, events = [], []
    monkeypatch.setattr(roles, "set_tier",
                        lambda inst, uid, tier, by: writes.append((inst, uid, tier, by)))
    monkeypatch.setattr(admin, "_emit_admin_event", lambda *a, **k: events.append(a))
    r = client.put(f"/v1/admin/users/{MEMBER}/role", json={"tier": "manager"},
                   headers={"X-Auth-User-Id": ADMIN})
    assert r.status_code == 200
    assert writes == [("sandbox", MEMBER, "manager", ADMIN)]
    assert len(events) == 1


def test_account_admin_cannot_assign_platform_operator(client, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "account_admin")
    r = client.put(f"/v1/admin/users/{MEMBER}/role", json={"tier": "platform_operator"},
                   headers={"X-Auth-User-Id": ADMIN})
    assert r.status_code == 403


def test_set_labels_admin_only(client, monkeypatch):
    monkeypatch.setattr(roles, "resolve_tier", lambda i, u: "member")
    r = client.put("/v1/admin/role-labels", json={"manager": "Team Lead"},
                   headers={"X-Auth-User-Id": MEMBER})
    assert r.status_code == 403
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_admin_api.py -v` → FAIL (`No module named 'src.api.admin'`).

- [ ] **Step 3: Implement `src/api/admin.py`**

```python
"""whoami + admin API for RBAC (Phase 2a). All /v1/admin/* require account_admin+.

User identity (email) for the admin listing comes from the Supabase admin API via
the service role; role/labels come from roles.py. Admin writes emit governance
events (the runs.py _write_event pattern) — role changes are security-relevant.
"""
import logging
import os

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.auth import require_bearer
from src.api import roles, authz

_logger = logging.getLogger(__name__)
router = APIRouter(tags=["admin"], dependencies=[Depends(require_bearer)])

_UNAUTH = JSONResponse({"detail": "Unauthorized"}, status_code=401)
_FORBIDDEN = JSONResponse({"detail": "Forbidden"}, status_code=403)


def _cfg(request):
    return request.app.state.config


def _uid(request) -> str:
    return request.headers.get("X-Auth-User-Id", "").strip()


def _require_admin(request):
    """Return (uid, tier) if caller is account_admin+, else a response to return."""
    uid = _uid(request)
    if not uid:
        return None, _UNAUTH
    tier = roles.resolve_tier(_cfg(request).instance_id, uid)
    if not roles.is_at_least(tier, "account_admin"):
        return None, _FORBIDDEN
    return (uid, tier), None


@router.get("/v1/whoami")
def whoami(request: Request):
    uid = _uid(request)
    if not uid:
        return _UNAUTH
    cfg = _cfg(request)
    tier = roles.resolve_tier(cfg.instance_id, uid)
    label = roles.get_labels(cfg.instance_id).get(tier, tier)
    return {"userId": uid, "tier": tier, "label": label,
            "reachableAgentIds": authz.reachable_agent_ids(request, cfg)}


def _supabase_users() -> dict[str, str]:
    """user_id -> email via the Supabase admin API (service role). Best-effort."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not (url and key):
        return {}
    resp = httpx.get(f"{url}/auth/v1/admin/users", params={"per_page": 200},
                     headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=10.0)
    resp.raise_for_status()
    data = resp.json()
    users = data.get("users", data if isinstance(data, list) else [])
    return {u["id"]: u.get("email", "") for u in users}


@router.get("/v1/admin/users")
def admin_users(request: Request):
    _, deny = _require_admin(request)
    if deny:
        return deny
    cfg = _cfg(request)
    role_map = roles.list_roles(cfg.instance_id)
    labels = roles.get_labels(cfg.instance_id)
    try:
        emails = _supabase_users()
    except Exception:
        _logger.warning("admin_users: supabase user list failed", exc_info=True)
        emails = {}
    out = []
    for uid, email in emails.items():
        tier = role_map.get(uid, "member")
        out.append({"userId": uid, "email": email, "tier": tier,
                    "label": labels.get(tier, tier)})
    return out


class RoleBody(BaseModel):
    tier: str


@router.put("/v1/admin/users/{user_id}/role")
def set_user_role(user_id: str, body: RoleBody, request: Request):
    caller, deny = _require_admin(request)
    if deny:
        return deny
    caller_uid, caller_tier = caller
    if body.tier not in roles.TIERS:
        return JSONResponse({"detail": "invalid tier"}, status_code=422)
    # Only a platform_operator may mint a platform_operator.
    if body.tier == "platform_operator" and not roles.is_at_least(caller_tier, "platform_operator"):
        return _FORBIDDEN
    roles.set_tier(_cfg(request).instance_id, user_id, body.tier, caller_uid)
    _emit_admin_event(request, "role.set", user_id, body.tier, caller_uid)
    return {"userId": user_id, "tier": body.tier}


@router.get("/v1/admin/role-labels")
def get_role_labels(request: Request):
    _, deny = _require_admin(request)
    if deny:
        return deny
    return roles.get_labels(_cfg(request).instance_id)


@router.put("/v1/admin/role-labels")
def put_role_labels(body: dict, request: Request):
    caller, deny = _require_admin(request)
    if deny:
        return deny
    labels = {t: str(l) for t, l in body.items() if t in roles.TIERS}
    roles.set_labels(_cfg(request).instance_id, labels)
    _emit_admin_event(request, "role_labels.set", None, ",".join(labels), caller[0])
    return roles.get_labels(_cfg(request).instance_id)


def _emit_admin_event(request, event_type, target_user, detail, actor) -> None:
    """Best-effort governance event for an admin write. Never raises."""
    try:
        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not (url and key):
            return
        httpx.post(
            f"{url}/rest/v1/governance_events",
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Content-Type": "application/json", "Prefer": "return=minimal"},
            json={"user_email": actor or "", "user_role": "account_admin",
                  "app": "admin", "event_type": event_type, "status": "ok",
                  "title": target_user, "findings": [], "content": detail, "run_id": None},
            timeout=10.0,
        ).raise_for_status()
    except Exception:
        _logger.warning("_emit_admin_event failed", exc_info=True)
```

- [ ] **Step 4: Wire the router** in `src/api/main.py`: add `from src.api.admin import router as admin_router` with the other imports and `app.include_router(admin_router)` with the others.

- [ ] **Step 5: Run** — `python -m pytest tests/test_admin_api.py -v` → PASS. `python -m pytest tests/ src/ -q` → no new failures.

- [ ] **Step 6: Commit**

```bash
git add src/api/admin.py src/api/main.py tests/test_admin_api.py
git commit -m "feat(admin): /v1/whoami + role/label admin API with governance audit (Phase 2a)"
```

---

### Task 8: Frontend — whoami-driven picker gating

**Files:**
- Modify: `D:\devprojects\ollie-hermes-frontend\src\adapters\orchestrator\OrchestratorClient.ts`
- Modify: `D:\devprojects\ollie-hermes-frontend\src\main.tsx`
- Test: `src\adapters\orchestrator\__tests__\OrchestratorClient.test.ts`

**Interfaces:**
- Consumes: `GET /v1/whoami` (Task 7) via `/orchestrator-proxy`.
- Produces: `OrchestratorClient.whoami(): Promise<{userId, tier, label, reachableAgentIds} | null>`; `main.tsx` filters the agents passed into the adapter to `reachableAgentIds` (fail-open to all agents if whoami is unavailable — the orchestrator still enforces, so a UI fallback can't leak).

- [ ] **Step 1: Write failing OrchestratorClient test** (follow the file's existing fetch-mock pattern):

```typescript
it('fetches whoami', async () => {
  fetchMock.mockResolvedValueOnce(okJson({ userId: 'u1', tier: 'member', label: 'Member', reachableAgentIds: ['default'] }));
  const c = new OrchestratorClient('/orchestrator-proxy');
  const who = await c.whoami();
  expect(fetchMock).toHaveBeenCalledWith('/orchestrator-proxy/v1/whoami');
  expect(who?.reachableAgentIds).toEqual(['default']);
});

it('whoami returns null on error', async () => {
  fetchMock.mockResolvedValueOnce({ ok: false, status: 401 });
  const c = new OrchestratorClient('/orchestrator-proxy');
  expect(await c.whoami()).toBeNull();
});
```
(`okJson` = the file's existing helper for a `{ok:true,json:...}` response.)

- [ ] **Step 2: Run to verify failure** — `npx vitest run src/adapters/orchestrator` → new tests FAIL.

- [ ] **Step 3: Implement `whoami` on `OrchestratorClient`** (match its method style):

```typescript
  async whoami(): Promise<{ userId: string; tier: string; label: string; reachableAgentIds: string[] } | null> {
    try {
      const res = await fetch(`${this.baseUrl}/v1/whoami`);
      if (!res.ok) return null;
      return await res.json();
    } catch {
      return null;
    }
  }
```

- [ ] **Step 4: Filter agents in `main.tsx`.** Before `createAdapter(config, orchestrator)`, when an orchestrator client exists, fetch whoami and narrow the agents. Since `main.tsx`'s top level isn't async, wrap the bootstrap in an async IIFE (or `.then`). Concretely: read `parseAgents(config.agents)`, call `await orchestrator?.whoami()`, and if it returns a non-null `reachableAgentIds`, set `config.agents = allAgents.filter(a => reachableAgentIds.includes(a.id))`; otherwise leave `config.agents` unchanged (fail-open — the orchestrator enforces regardless). Then proceed with the existing `createAdapter` + `createRoot(...).render(...)`. Keep the existing render otherwise identical.

- [ ] **Step 5: Run** — `npx vitest run` (bash shell) → ALL PASS. `npx tsc --noEmit` → clean.

- [ ] **Step 6: Commit**

```bash
git add src/adapters/orchestrator/OrchestratorClient.ts src/adapters/orchestrator/__tests__/OrchestratorClient.test.ts src/main.tsx
git commit -m "feat(picker): render only reachable agents via /v1/whoami (Phase 2a)"
```

---

### Task 9: Rollout runbook

**Files:**
- Create: `D:\devprojects\ollie-hermes-orchestrator\docs\runbooks\rbac-phase2a-rollout.md`

**Interfaces:** Pure documentation. No code, no tests.

- [ ] **Step 1: Write the runbook** covering, sandbox box (`ollie@178.105.216.167`) FIRST, jnow only after sandbox smoke passes:

1. **Apply migration** `0012_user_roles.sql` to Supabase project `kpdqhntsvjzhqjeupzsj` (SQL editor), same as prior `development/core` migrations.
2. **Set `INSTANCE_ID`** in each box's orchestrator env (`~/.config/ollie-orchestrator/.env`): `INSTANCE_ID=sandbox` (sandbox) / `INSTANCE_ID=jnow` (prod). Note the value — every role row is scoped by it.
3. **Mark Ollie user-scoped:** in `~/hermes-stack/.env` `AGENTS_JSON`, add `"scope":"user"` to the `default` (Ollie) entry; leave the others to default `company` (add `"manager_visible":true` to any a manager should reach). Rebuild the frontend image only if the SPA needs the scope (it doesn't for 2a — whoami drives the picker), so a box-side `AGENTS_JSON` edit + orchestrator restart is enough for enforcement; the frontend picks up reachable agents from whoami.
4. **Seed roles (fail-closed bootstrap):** the table is empty, so everyone (including you) resolves to `member` and would lose company-agent access. Seed your own `account_admin`/`platform_operator` row FIRST, before relying on the admin API:
   ```sql
   insert into public.user_roles (instance_id, user_id, tier)
   values ('sandbox', '1a2b341c-0d01-418f-9fdb-4cebc27058c7', 'platform_operator')
   on conflict (instance_id, user_id) do update set tier = excluded.tier;
   ```
5. **Restart the orchestrator** (`systemctl --user restart ollie-orchestrator`); verify:
   ```bash
   curl -s -H "Authorization: Bearer $ORCHESTRATOR_KEY" -H "X-Auth-User-Id: <john-uuid>" \
        http://127.0.0.1:9123/v1/whoami
   ```
   Expect your tier + full `reachableAgentIds`.
6. **Deploy the frontend** (rebuild `justnorthow/ollie-hermes-frontend`, tag `:rollback-pre-rbac`, keep the sandbox's own tag convention — do NOT clobber a tag prod shares) so the picker reads whoami.
7. **Smoke tests (on-box, `http://127.0.0.1:9123`, plus the UI):**
   - `whoami` as John (operator) → all agents; as a throwaway member (no row) → tier `member`, `reachableAgentIds` = `["default"]` only.
   - Member posting a run to a company agent (e.g. `POST /v1/runs/olivia-marketing` with the member's `X-Auth-User-Id`) → `403 Forbidden`; to `default` → allowed.
   - Member `GET /v1/sessions/olivia-marketing` → `403`.
   - Member `GET /v1/admin/users` → `403`; John → `200`.
   - `PUT /v1/admin/users/<member>/role {"tier":"manager"}` as John → `200` + a `governance_events` row; the member's `whoami` now includes any `manager_visible` company agents (within the 30s cache TTL).
   - Phase 1 still holds: an allowed agent + a foreign session id → still `403` not-found (ownership independent of RBAC).
   - In the UI: log in as a member → picker shows only Ollie; as John → all agents.
8. **Rollback:** retag the frontend image; `git checkout <pre-rollout-sha>` the orchestrator + restart; the migration is additive and can stay; roles rows are inert without the resolving code.

- [ ] **Step 2: Commit**

```bash
git add docs/runbooks/rbac-phase2a-rollout.md
git commit -m "docs: rollout runbook for RBAC Phase 2a"
```

---

## Self-Review Notes

- **Spec coverage:** role model = Tasks 2,3 (INSTANCE_ID, tiers/labels/resolution); data model = Task 1; scope taxonomy = Task 4; resolution/JWT-no-longer-trusted = Task 3 + Task 6 (uses user_id not X-Auth-Role); enforcement = Tasks 5,6; whoami = Task 7; admin API = Task 7; frontend picker = Task 8; rollout ordering = Task 9. Manager subset via `manager_visible` = Tasks 4,5. Governance audit of admin writes = Task 7.
- **Known v1 boundaries (spec-consistent):** capabilities fixed in code (no runtime permission editor); manager access is agent-config (`manager_visible`), not per-user ACL; role cache TTL 30s (a role change lags up to 30s — documented); admin user listing depends on the Supabase admin API (best-effort, degrades to role-only rows).
- **Type consistency:** `resolve_tier(instance_id, user_id)`, `is_at_least(tier, minimum)`, `can_reach(tier, scope, manager_visible)`, `check_agent_access(request, agent_id, cfg)`, `reachable_agent_ids(request, cfg)`, and the `whoami` JSON shape `{userId,tier,label,reachableAgentIds}` are consistent across Tasks 3/5/7/8. Tiers string set identical everywhere: `member/manager/account_admin/platform_operator`.
- **Fail-open note (Task 8):** the frontend picker fails *open* (shows all agents if whoami is down) on purpose — the orchestrator's 403 is the real gate, so a UI fallback cannot leak access. Called out so a reviewer doesn't read it as a hole.
