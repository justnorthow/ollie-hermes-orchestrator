# Orchestrator-Maintained Proxy Maps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make agent create/delete keep `HERMES_GATEWAY_URLS` / `HERMES_DASHBOARD_URLS` current, and repair existing drift on orchestrator startup, so a box stays done-done without hand remediation.

**Architecture:** One new module `src/proxy_maps.py` exposing a single primitive, `sync()`, that makes the two maps in the orchestrator's own `.env` cover every agent in `AGENTS_JSON` while preserving operator-pinned entries. `lifecycle.create_agent` and `lifecycle.delete_agent` call it after they mutate `AGENTS_JSON`; `create_app` calls it once at startup to heal pre-existing drift. All calls are best-effort — a failure logs and continues, because `agents_json.loopback_url_for()` already keeps routing correct.

**Tech Stack:** Python 3.11+, FastAPI, pytest (`asyncio_mode = auto`, `pythonpath = .`).

## Global Constraints

- Map values are `http://127.0.0.1:<port>` — byte-identical to what `render-proxy-maps.py` writes, so `check-box-config.sh` stays satisfied.
- Add-if-missing only. Never overwrite an entry that already exists (operator entries win). Removal happens only via explicit `drop_ids`.
- Every call site is best-effort: wrap in `try/except`, log a warning, continue. An `.env` write must never roll back a created agent or prevent startup.
- No new SSE step in `create_agent` — the frontend's create modal has eight hardcoded steps.
- Do not mutate `os.environ` after writing.
- Writes go through the existing `agents_json.set_env_key()`; do not add a second env writer.
- Branch: `fix/orchestrator-proxy-map-maintenance`, base `0d8caf5`. Test baseline before any change: **557 passed, 1 skipped**.

## Deviation from the spec

The spec proposed three functions (`upsert_agent`, `remove_agent`, `reconcile_all`). This plan collapses them into one `sync()` taking the full agent list, because all three cases are the same operation over a different agent list, and because a single-agent upsert cannot sensibly repair a corrupt map value (it has no way to reconstruct the other entries). Every specced semantic is preserved. Approved deviation — confirm before implementing if you are reading this cold.

## File Structure

| File | Responsibility |
|---|---|
| `src/proxy_maps.py` (create) | Sole owner of the two proxy-map keys in the orchestrator `.env`. |
| `src/config.py` (modify) | Gains `orch_env_path`, honouring `ORCH_ENV`. |
| `src/lifecycle.py` (modify) | Calls `sync()` after create, delete, and create-rollback. |
| `src/api/main.py` (modify) | Calls `sync()` once at startup. |
| `tests/test_proxy_maps.py` (create) | Unit coverage for `sync()`. |
| `tests/test_config.py` (modify or create) | `orch_env_path` default and `ORCH_ENV` override. |
| `tests/test_lifecycle_proxy_maps.py` (create) | create/delete call through. |
| 6 existing test files (modify) | Add the new required `Config` kwarg. |

---

### Task 1: `Config.orch_env_path`

**Files:**
- Modify: `src/config.py:10-41`
- Modify: `tests/test_appdata_api.py:19`, `tests/test_apps_api.py:20`, `tests/test_auth.py:11`, `tests/test_folders_api.py:13`, `tests/test_folders_store.py:8`, `tests/test_robustness.py:23`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Config.orch_env_path: Path` — every later task reads the orchestrator `.env` location from this field.

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py` (if it already exists, append these two tests):

```python
import os
from pathlib import Path

from src.config import Config


def test_orch_env_path_defaults_under_home(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCHESTRATOR_KEY", "topsecret")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ORCH_ENV", raising=False)
    cfg = Config.load()
    assert cfg.orch_env_path == tmp_path / ".config" / "ollie-orchestrator" / ".env"


def test_orch_env_path_honours_orch_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCHESTRATOR_KEY", "topsecret")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ORCH_ENV", str(tmp_path / "custom.env"))
    cfg = Config.load()
    assert cfg.orch_env_path == tmp_path / "custom.env"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'orch_env_path'`

- [ ] **Step 3: Add the field**

In `src/config.py`, add the field to the dataclass after `instance_id`:

```python
    instance_id: str
    orch_env_path: Path
```

In `load()`, after the `instance_id` line:

```python
        orch_env = Path(os.environ.get(
            "ORCH_ENV", home / ".config" / "ollie-orchestrator" / ".env"))
```

and add to the `cls(...)` call:

```python
            instance_id=instance_id,
            orch_env_path=orch_env,
```

`ORCH_ENV` is deliberately the same variable `scripts/lib/render-proxy-maps.py` reads, so both writers target one file.

- [ ] **Step 4: Update the six direct `Config(...)` constructions**

Each of these builds `Config` with keyword arguments and will now fail with a missing-argument `TypeError`. In each, add one line after `instance_id=...`:

```python
        orch_env_path=tmp_path / "orch.env",
```

Files and lines: `tests/test_appdata_api.py:19`, `tests/test_apps_api.py:20`, `tests/test_auth.py:11`, `tests/test_folders_api.py:13`, `tests/test_folders_store.py:8`, `tests/test_robustness.py:23`. Each fixture already has `tmp_path` in scope; if one does not, use `Path("/nonexistent/orch.env")` — `sync()` no-ops on an absent file, so these tests stay unaffected either way.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS, 559 passed, 1 skipped (baseline 557 + the 2 new config tests).

- [ ] **Step 6: Commit**

```bash
git add src/config.py tests/test_config.py tests/test_appdata_api.py tests/test_apps_api.py tests/test_auth.py tests/test_folders_api.py tests/test_folders_store.py tests/test_robustness.py
git commit -m "feat(config): add orch_env_path honouring ORCH_ENV

The orchestrator needs to locate its own .env to maintain the
HERMES_GATEWAY_URLS / HERMES_DASHBOARD_URLS proxy maps. ORCH_ENV is the
same variable scripts/lib/render-proxy-maps.py reads, so the install-time
writer and the runtime writer target one file.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: `src/proxy_maps.py`

**Files:**
- Create: `src/proxy_maps.py`
- Test: `tests/test_proxy_maps.py`

**Interfaces:**
- Consumes: `Config.orch_env_path` (Task 1); `agents_json.AgentEntry`, `agents_json.set_env_key(env_path: Path, key: str, value: str) -> None`.
- Produces: `sync(env_path: Path, agents: list[AgentEntry], *, drop_ids: tuple[str, ...] = ()) -> dict[str, list[str]]` returning `{"added": [...], "dropped": [...]}` with sorted, de-duplicated id lists. Tasks 3 and 4 call exactly this.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_proxy_maps.py`:

```python
import json
from pathlib import Path

import pytest

from src.agents_json import AgentEntry
from src import proxy_maps


GW = "HERMES_GATEWAY_URLS"
DASH = "HERMES_DASHBOARD_URLS"


def _agent(agent_id, gw, dash):
    return AgentEntry(id=agent_id, name=agent_id, gateway_port=gw,
                      dashboard_port=dash, color="#888888")


def _read_key(path, key):
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            return json.loads(line[len(key) + 1:])
    return None


@pytest.fixture
def env(tmp_path):
    p = tmp_path / "orch.env"
    p.write_text("ORCHESTRATOR_KEY=x\n", encoding="utf-8")
    return p


def test_adds_missing_agents_to_both_maps(env):
    result = proxy_maps.sync(env, [_agent("default", 8642, 9119),
                                   _agent("mail-agent", 8643, 9121)])
    assert _read_key(env, GW) == {"default": "http://127.0.0.1:8642",
                                  "mail-agent": "http://127.0.0.1:8643"}
    assert _read_key(env, DASH) == {"default": "http://127.0.0.1:9119",
                                    "mail-agent": "http://127.0.0.1:9121"}
    assert result["added"] == ["default", "mail-agent"]


def test_never_overwrites_an_operator_pinned_entry(env):
    env.write_text(
        f'{GW}={{"default": "http://10.0.0.5:8642"}}\n', encoding="utf-8")
    proxy_maps.sync(env, [_agent("default", 8642, 9119)])
    assert _read_key(env, GW) == {"default": "http://10.0.0.5:8642"}


def test_preserves_unrelated_operator_entries_when_adding(env):
    env.write_text(
        f'{GW}={{"legacy": "http://127.0.0.1:8000"}}\n', encoding="utf-8")
    proxy_maps.sync(env, [_agent("default", 8642, 9119)])
    assert _read_key(env, GW) == {"legacy": "http://127.0.0.1:8000",
                                  "default": "http://127.0.0.1:8642"}


def test_drop_ids_removes_the_entry(env):
    proxy_maps.sync(env, [_agent("default", 8642, 9119),
                          _agent("mail-agent", 8643, 9121)])
    result = proxy_maps.sync(env, [_agent("default", 8642, 9119)],
                             drop_ids=("mail-agent",))
    assert _read_key(env, GW) == {"default": "http://127.0.0.1:8642"}
    assert _read_key(env, DASH) == {"default": "http://127.0.0.1:9119"}
    assert result["dropped"] == ["mail-agent"]


def test_regenerates_an_unparseable_value(env):
    env.write_text(f"{GW}=not-json\n", encoding="utf-8")
    proxy_maps.sync(env, [_agent("default", 8642, 9119)])
    assert _read_key(env, GW) == {"default": "http://127.0.0.1:8642"}


def test_regenerates_a_non_object_value(env):
    env.write_text(f"{GW}=[]\n", encoding="utf-8")
    proxy_maps.sync(env, [_agent("default", 8642, 9119)])
    assert _read_key(env, GW) == {"default": "http://127.0.0.1:8642"}


def test_is_idempotent(env):
    agents = [_agent("default", 8642, 9119)]
    proxy_maps.sync(env, agents)
    before = env.read_text(encoding="utf-8")
    result = proxy_maps.sync(env, agents)
    assert env.read_text(encoding="utf-8") == before
    assert result == {"added": [], "dropped": []}


def test_absent_file_is_a_no_op(tmp_path):
    missing = tmp_path / "nope" / "orch.env"
    assert proxy_maps.sync(missing, [_agent("default", 8642, 9119)]) == {
        "added": [], "dropped": []}
    assert not missing.exists()


def test_written_value_is_single_line(env):
    proxy_maps.sync(env, [_agent("default", 8642, 9119)])
    gw_lines = [l for l in env.read_text(encoding="utf-8").splitlines()
                if l.startswith(GW + "=")]
    assert len(gw_lines) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_proxy_maps.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.proxy_maps'`

- [ ] **Step 3: Write the implementation**

Create `src/proxy_maps.py`:

```python
"""Maintenance of the orchestrator's HERMES_GATEWAY_URLS / HERMES_DASHBOARD_URLS
proxy maps.

These map an agent id to its loopback gateway/dashboard URL. Historically they
were written ONLY by the install scripts at provision time
(detect_agents | scripts/lib/render-proxy-maps.py), so an agent created later
through the dashboard UI or ollie-fleetctl was absent from both and
check-box-config.sh reported the box as not done-done. Chat still worked, because
agents_json.loopback_url_for() derives the URL from AGENTS_JSON — see the prod
'pam' incident, 2026-07-17.

The rule here is deliberately add-if-missing rather than the install script's
regenerate-if-not-covering. Driven per-operation, a full re-render would leave a
deleted agent's entry behind (its map still "covers" every remaining id, so it is
kept) and would discard operator-pinned values on the regenerate branch. Removal
is therefore explicit, via drop_ids.
"""
import json
import logging
from pathlib import Path

from src.agents_json import AgentEntry, set_env_key

_logger = logging.getLogger(__name__)

GATEWAY_KEY = "HERMES_GATEWAY_URLS"
DASHBOARD_KEY = "HERMES_DASHBOARD_URLS"


def _read_map(env_path: Path, key: str) -> dict:
    """Parse one JSON object out of the .env, or {} if it is absent, unparseable
    or not an object — all of which mean 'nothing trustworthy here', and the
    caller then repopulates from AGENTS_JSON."""
    value = None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            value = line[len(key) + 1:]
    if value is None:
        return {}
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def sync(env_path: Path, agents: list[AgentEntry], *,
         drop_ids: tuple[str, ...] = ()) -> dict[str, list[str]]:
    """Make both proxy maps cover every agent, preserving operator entries.

    Existing entries are never overwritten, so an operator who pinned a custom
    URL keeps it. Entries are removed only when named in drop_ids. Returns
    {"added": [...], "dropped": [...]} for logging.
    """
    if not env_path.exists():
        # A box without an orchestrator .env is not one we should be creating
        # one for; the install scripts own that file's existence.
        _logger.warning("proxy map sync skipped: %s does not exist", env_path)
        return {"added": [], "dropped": []}

    added: list[str] = []
    dropped: list[str] = []
    for key, port_attr in ((GATEWAY_KEY, "gateway_port"),
                           (DASHBOARD_KEY, "dashboard_port")):
        current = _read_map(env_path, key)
        updated = dict(current)
        for agent_id in drop_ids:
            if updated.pop(agent_id, None) is not None:
                dropped.append(agent_id)
        for entry in agents:
            if entry.id in updated:
                continue
            updated[entry.id] = f"http://127.0.0.1:{getattr(entry, port_attr)}"
            added.append(entry.id)
        if updated != current:
            set_env_key(env_path, key, json.dumps(updated))
    return {"added": sorted(set(added)), "dropped": sorted(set(dropped))}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_proxy_maps.py -v`
Expected: PASS, 9 passed.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS, 568 passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/proxy_maps.py tests/test_proxy_maps.py
git commit -m "feat(proxy-maps): add sync() to maintain the orchestrator proxy maps

Add-if-missing over both map keys, with explicit removal via drop_ids.
Deliberately not a port of render-proxy-maps.py's regenerate-if-not-covering
rule: driven per-operation that leaves a deleted agent's entry behind (the
map still covers every remaining id, so it is kept) and discards
operator-pinned values on the regenerate branch.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Wire create, delete and rollback

**Files:**
- Modify: `src/lifecycle.py:6` (import), `:150-152` (create), `:200-205` (rollback), `:266-269` (delete)
- Test: `tests/test_lifecycle_proxy_maps.py`

**Interfaces:**
- Consumes: `proxy_maps.sync(...)` (Task 2), `Config.orch_env_path` (Task 1), existing `agents_json.read_agents(env_path) -> list[AgentEntry]`.
- Produces: no new public API.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lifecycle_proxy_maps.py`:

```python
import json

import pytest

from src.lifecycle import CreateRequest, create_agent, delete_agent


GW = "HERMES_GATEWAY_URLS"


def _read_key(path, key):
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            return json.loads(line[len(key) + 1:])
    return None


@pytest.fixture
def orch_env(monkeypatch, tmp_path):
    p = tmp_path / "orch.env"
    p.write_text("ORCHESTRATOR_KEY=x\n", encoding="utf-8")
    monkeypatch.setenv("ORCH_ENV", str(p))
    return p


def _req(name="mail-agent"):
    return CreateRequest(
        name=name, display_name="Karl M", color=None, provider="openai",
        model="gpt-5.6-sol", api_key="k", system_prompt=None,
        enabled_skills=[], api_server_key="gk", auth_method="inherit",
    )


async def test_create_adds_the_agent_to_the_proxy_maps(fake_env, orch_env):
    events = [ev async for ev in create_agent(_req())]
    assert events[-1]["event"] == "done", events[-1]
    assert "mail-agent" in _read_key(orch_env, GW)


async def test_delete_removes_the_agent_from_the_proxy_maps(fake_env, orch_env):
    events = [ev async for ev in create_agent(_req())]
    assert events[-1]["event"] == "done", events[-1]
    assert (await delete_agent("mail-agent"))["ok"] is True
    assert "mail-agent" not in _read_key(orch_env, GW)


async def test_create_still_succeeds_when_map_sync_fails(
        fake_env, orch_env, monkeypatch):
    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr("src.lifecycle.proxy_maps.sync", boom)
    events = [ev async for ev in create_agent(_req())]
    assert events[-1]["event"] == "done", events[-1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_lifecycle_proxy_maps.py -v`
Expected: FAIL — the first two assert on `"mail-agent"` being present/absent in a map that is never written, so `_read_key` returns `None` and raises `TypeError: argument of type 'NoneType' is not iterable`.

- [ ] **Step 3: Add the import**

In `src/lifecycle.py`, after the existing `from src.agents_json import ...` on line 6:

```python
from src import proxy_maps
```

- [ ] **Step 4: Wire the create path**

In `create_agent`, replace lines 150-152:

```python
            write_agent(env_path, entry)
            yield _ev("update_agents_json")
            completed_steps.append("update_agents_json")
```

with:

```python
            write_agent(env_path, entry)
            # Keep the orchestrator's proxy maps covering the new agent. Folded
            # into this step rather than emitted as its own SSE event: the
            # frontend's create modal has eight hardcoded steps and a ninth
            # would desynchronise it. Best-effort — loopback_url_for() already
            # resolves this agent from AGENTS_JSON, so a failure here costs only
            # gate cleanliness, never a working agent.
            try:
                proxy_maps.sync(cfg.orch_env_path, read_agents(env_path))
            except Exception:
                _logger.warning("proxy map sync failed after create", exc_info=True)
            yield _ev("update_agents_json")
            completed_steps.append("update_agents_json")
```

- [ ] **Step 5: Wire the rollback path**

In `_rollback_create`, replace the `update_agents_json` block at lines 201-205:

```python
    if "update_agents_json" in completed_steps:
        try:
            remove_agent(env_path, name)
        except Exception:
            _logger.warning("rollback: remove_agent failed", exc_info=True)
```

with:

```python
    if "update_agents_json" in completed_steps:
        try:
            remove_agent(env_path, name)
        except Exception:
            _logger.warning("rollback: remove_agent failed", exc_info=True)
        try:
            from src.config import Config as _Config
            proxy_maps.sync(_Config.load().orch_env_path,
                            read_agents(env_path), drop_ids=(name,))
        except Exception:
            _logger.warning("rollback: proxy map sync failed", exc_info=True)
```

- [ ] **Step 6: Wire the delete path**

In `delete_agent`, replace lines 266-269:

```python
        try:
            remove_agent(env_path, agent_id)
        except Exception:
            _logger.warning("delete: AGENTS_JSON failed", exc_info=True)
```

with:

```python
        try:
            remove_agent(env_path, agent_id)
        except Exception:
            _logger.warning("delete: AGENTS_JSON failed", exc_info=True)
        try:
            proxy_maps.sync(cfg.orch_env_path, read_agents(env_path),
                            drop_ids=(agent_id,))
        except Exception:
            _logger.warning("delete: proxy map sync failed", exc_info=True)
```

- [ ] **Step 7: Run the new tests**

Run: `python -m pytest tests/test_lifecycle_proxy_maps.py -v`
Expected: PASS, 3 passed.

- [ ] **Step 8: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS, 571 passed, 1 skipped.

- [ ] **Step 9: Commit**

```bash
git add src/lifecycle.py tests/test_lifecycle_proxy_maps.py
git commit -m "fix(agents): keep proxy maps current across create and delete

An agent created through the UI or ollie-fleetctl was absent from
HERMES_GATEWAY_URLS / HERMES_DASHBOARD_URLS, leaving check-box-config.sh
reporting not done-done. Hit Towns 2026-07-27 and eSource/GetBilled
2026-07-28, both repaired by hand with the same three commands.

Folded into the existing update_agents_json step rather than emitted as a
ninth SSE event, which would desynchronise the frontend's eight-step create
modal. Best-effort throughout: loopback_url_for() already resolves agents
from AGENTS_JSON, so a failed write costs gate cleanliness, not a working
agent.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Startup reconcile

**Files:**
- Modify: `src/api/main.py:1` (imports), `:24-29` (`create_app`)
- Test: `tests/test_startup_reconcile.py`

**Interfaces:**
- Consumes: `proxy_maps.sync(...)` (Task 2), `Config.orch_env_path` (Task 1).
- Produces: no new public API.

- [ ] **Step 1: Write the failing test**

Create `tests/test_startup_reconcile.py`:

```python
import json

import pytest

from src.api.main import create_app


GW = "HERMES_GATEWAY_URLS"


def _read_key(path, key):
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            return json.loads(line[len(key) + 1:])
    return None


@pytest.fixture
def orch_env(monkeypatch, tmp_path):
    p = tmp_path / "orch.env"
    p.write_text("ORCHESTRATOR_KEY=x\n", encoding="utf-8")
    monkeypatch.setenv("ORCH_ENV", str(p))
    return p


def test_startup_backfills_an_agent_missing_from_the_maps(fake_env, orch_env):
    stack_env = fake_env["stack"] / ".env"
    stack_env.write_text(
        "HERMES_GATEWAY_KEY=k\n"
        'AGENTS_JSON=[{"id":"default","name":"Billie",'
        '"gatewayUrl":"http://host.docker.internal:8642",'
        '"dashboardUrl":"http://host.docker.internal:9119","color":"#888888"}]\n',
        encoding="utf-8")
    create_app()
    assert _read_key(orch_env, GW) == {"default": "http://127.0.0.1:8642"}


def test_startup_survives_a_map_sync_failure(fake_env, orch_env, monkeypatch):
    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr("src.api.main.proxy_maps.sync", boom)
    assert create_app() is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_startup_reconcile.py -v`
Expected: FAIL — first test fails with `assert None == {...}` because nothing writes the map at startup.

- [ ] **Step 3: Add imports**

At the top of `src/api/main.py`, after `import os`:

```python
import logging
```

and after `from src.config import Config`:

```python
from src import proxy_maps
from src.agents_json import read_agents
```

Then below the imports, before `def create_app()`:

```python
_logger = logging.getLogger(__name__)
```

- [ ] **Step 4: Call sync at startup**

In `create_app`, after `app.state.rate_bucket = TokenBucket(rate_per_min=10)`:

```python
    # Repair proxy-map drift left by any agent created before this orchestrator
    # learned to maintain the maps itself, on any box. Add-if-missing, so an
    # operator's pinned entry is never disturbed. Best-effort: an .env write must
    # never stop the service starting, and loopback_url_for() keeps routing
    # correct regardless. The rewrite lands one restart ahead of this process,
    # which already read its environment — that is intentional, see the spec.
    try:
        result = proxy_maps.sync(
            cfg.orch_env_path, read_agents(cfg.hermes_stack_dir / ".env"))
        if result["added"]:
            _logger.info("proxy maps: backfilled %s", ", ".join(result["added"]))
    except Exception:
        _logger.warning("proxy map reconcile at startup failed", exc_info=True)
```

- [ ] **Step 5: Run the new tests**

Run: `python -m pytest tests/test_startup_reconcile.py -v`
Expected: PASS, 2 passed.

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS, 573 passed, 1 skipped.

- [ ] **Step 7: Commit**

```bash
git add src/api/main.py tests/test_startup_reconcile.py
git commit -m "feat(agents): reconcile proxy maps at orchestrator startup

Repairs drift left by any agent created before the orchestrator maintained
the maps itself, on any box, without touching operator-pinned entries. Means
rollout heals every box as it restarts, so the done-done gate does not need
relaxing to accept the AGENTS_JSON fallback.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Push and roll out

**Files:** none — deployment only.

**Interfaces:**
- Consumes: everything above, merged to `main`.
- Produces: four boxes running the new orchestrator, each reporting done-done.

- [ ] **Step 1: Verify the branch is green and merge to main**

```bash
python -m pytest -q
git checkout main && git merge --ff-only fix/orchestrator-proxy-map-maintenance && git push origin main
```

Expected: 573 passed, 1 skipped; fast-forward merge; push succeeds.

- [ ] **Step 2: Deploy each box**

Boxes and their SSH targets:

| Box | Target |
|---|---|
| eSource / GetBilled | `ollie-getbilled` |
| jnow prod | `ollie@46.224.81.84` |
| sandbox | `ollie@178.105.216.167` (`-i ~/.ssh/ollie_sandbox`) |
| Towns | `ollie@204.168.152.243` (`-i ~/.ssh/Ollie-Hermes.pem`) |

For each, run the idempotent installer, which does `fetch` + `reset --hard origin/main`, rebuilds the venv, and restarts the service:

```bash
ssh ollie-getbilled 'cd ~/ollie-hermes-install && git pull --ff-only && bash scripts/05-install-orchestrator.sh'
```

Use `-o IdentityAgent=none` on any host not covered by an `~/.ssh/config` entry — the 1Password agent intercepts otherwise.

- [ ] **Step 3: Verify each box**

```bash
ssh ollie-getbilled 'systemctl --user is-active ollie-orchestrator && grep -E "HERMES_GATEWAY_URLS|HERMES_DASHBOARD_URLS" ~/.config/ollie-orchestrator/.env && OPERATOR_EMAIL=jb@jnow.io bash ~/ollie-hermes-install/scripts/check-box-config.sh | tail -3'
```

Expected on each: `active`, both maps covering every agent on that box, and `OK: box config is done-done`.

- [ ] **Step 4: Confirm the startup backfill actually fired**

```bash
ssh ollie-getbilled 'journalctl --user -u ollie-orchestrator --since "10 min ago" --no-pager | grep -i "proxy maps"'
```

Expected: either a `proxy maps: backfilled ...` line, or no line at all on a box whose maps were already complete. A `reconcile at startup failed` warning means stop and investigate before continuing to the next box.

---

## Self-Review

**Spec coverage.** Surgical add-if-missing → Task 2. Delete removes the entry → Tasks 2, 3. Startup reconcile → Task 4. `orch_env_path` honouring `ORCH_ENV` → Task 1. Writes via `set_env_key` → Task 2. No new SSE step → Task 3 Step 4. Best-effort everywhere → Tasks 3, 4. No `os.environ` mutation → absent by construction, noted in Task 4's comment. Rollout → Task 5.

**Deviation.** The spec's three-function API is one `sync()`. Flagged at the top of this plan.

**Type consistency.** `sync(env_path, agents, *, drop_ids)` returning `{"added": [...], "dropped": [...]}` is used identically in Tasks 2, 3 and 4. `AgentEntry.gateway_port` / `.dashboard_port` match `src/agents_json.py:14-26`. `read_agents(env_path) -> list[AgentEntry]` matches `src/agents_json.py:74`. `set_env_key(env_path, key, value)` matches `src/agents_json.py:124`.

**Known gap.** Task 3's rollback path re-loads `Config` locally rather than threading it in, because `_rollback_create(name, completed_steps, env_path)` does not currently receive it. Widening that signature is a larger change than this fix warrants; the local import mirrors the existing local-import pattern in `agents_json.loopback_url_for()`.
