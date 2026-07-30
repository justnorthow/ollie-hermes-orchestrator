# Switchable Dispatch — Slices 1–2 (direct-only) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one agent on an Ollie box ask another a question inline — Billie consults Karl and gets the answer in the same turn — mediated by the orchestrator so the asking agent can never lie about who it acts for.

**Architecture:** A Hermes tool plugin exposes `list_teammates` / `ask_teammate` to the model. The plugin never touches a peer gateway; it POSTs to the orchestrator, which resolves the originating human from `agent_sessions`, enforces caps, audits to `governance_events`, and only then calls the target agent's `/v1/chat/completions`. Two modes ship: `off` (genuinely inert) and `direct` (synchronous consult). No queue, no database schema change, no install-repo change.

**Tech Stack:** Python 3.11+, FastAPI, `httpx`, pytest (`pythonpath = .`, `asyncio_mode = auto`). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-30-switchable-dispatch-design.md`

## Global Constraints

- **No new runtime dependencies.** `httpx`, `fastapi`, `pyyaml` are already in `requirements.txt`.
- **The plugin never calls a peer gateway directly.** All routing goes through the orchestrator. `HERMES_GATEWAY_KEY` is one shared key across every profile on the box, so peer-to-peer would be unauthenticated in any sense that matters.
- **Provenance is resolved, never asserted.** The caller supplies `agent_id` + `session_id`; the orchestrator derives `user_id` via the existing `get_session_owner`. A caller-supplied user identity must be rejected if present, not trusted.
- **Fail closed.** If provenance does not resolve, refuse the request. Never default to a permissive identity.
- **Authority never escalates.** Effective authority is the origin human's tier and nothing may exceed it.
- **`off` must be genuinely inert** — no tool schemas, no system-prompt block. Existing boxes (Towns, jnow prod) must see byte-identical agent tool lists and system prompts.
- **Every dispatch attempt writes a `governance_events` row** with `app='dispatch'`. No parallel audit log.
- **Consult is permitted only to `speed_class: fast` peers.** A `heavy` peer converts to a refusal naming the reason, never a silent failure.
- **Structured failures only.** Every failure returns `{"ok": false, "reason": "<enum>"}` — never an empty string, never a raw exception, never a fabricated answer.
- **All tests run offline with no credentials.** Every network call is injected.
- **Zero upstream `hermes-agent` patches.** The plugin installs via `hermes plugins install`.

---

## File Structure

| Path | Responsibility |
|---|---|
| `src/dispatch/__init__.py` | Package marker. |
| `src/dispatch/types.py` | `DispatchMode`, `ConsultRequest`, `ConsultResult`, `Teammate`, refusal reason constants. No logic. |
| `src/dispatch/roster.py` | Reads `AGENTS_JSON` + `src/catalog.py`, produces `Teammate` records with `consult_eligible`. No network. |
| `src/dispatch/authority.py` | Provenance resolution and caps. Pure except for the one `get_session_owner` call, which is injected. |
| `src/dispatch/audit.py` | `governance_events` writes. The only module that knows the audit wire format. |
| `src/dispatch/backends.py` | `off` and `direct` drivers behind one interface. `direct` owns the peer-gateway call. |
| `src/api/dispatch.py` | FastAPI router: `GET /v1/dispatch/teammates`, `POST /v1/dispatch/consult`. |
| `src/api/main.py` | **Modify** — register the router. |
| `plugins/dispatch/plugin.yaml` | Plugin manifest. |
| `plugins/dispatch/__init__.py` | Plugin registration. |
| `plugins/dispatch/provider.py` | Tool schemas, `handle_tool_call`, `system_prompt_block`, `get_config_schema`. |
| `plugins/dispatch/http_client.py` | Thin orchestrator client. The only plugin file that does I/O. |
| `tests/test_dispatch_types.py` … `tests/test_plugin_dispatch.py` | One test file per module. |

Rationale for splitting `src/dispatch/` into five small modules rather than one: `authority.py` and `roster.py` are pure and are where the security properties live, so they must be readable in isolation; `backends.py` is the only module that talks to a gateway; `audit.py` is the only one that writes. That boundary is what makes the whole suite runnable offline.

---

### Task 1: Types and refusal vocabulary

**Files:**
- Create: `src/dispatch/__init__.py`, `src/dispatch/types.py`
- Test: `tests/test_dispatch_types.py`

**Interfaces:**
- Produces:
  - `MODE_OFF = "off"`, `MODE_DIRECT = "direct"`, `MODE_LOCAL = "local"`, `MODE_LINEAR = "linear"`, `VALID_MODES: frozenset[str]`
  - `REASON_NOT_ENABLED`, `REASON_FORBIDDEN`, `REASON_UNKNOWN_PEER`, `REASON_PEER_NOT_CONSULT_ELIGIBLE`, `REASON_CAP_EXCEEDED`, `REASON_TIMEOUT`, `REASON_PEER_UNAVAILABLE` — all `str`
  - `Teammate(agent_id: str, display_name: str, subtitle: str | None, model: str | None, speed_class: str | None, consult_eligible: bool)` — frozen dataclass
  - `ConsultRequest(from_agent: str, session_id: str, to_agent: str, question: str, chain: tuple[str, ...] = ())` — frozen dataclass
  - `ConsultResult(ok: bool, answer: str | None = None, reason: str | None = None, detail: str = "", peer: str | None = None)` — frozen dataclass, with classmethods `granted(answer, peer)` and `refused(reason, detail="", peer=None)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dispatch_types.py`:

```python
from src.dispatch.types import (
    MODE_DIRECT,
    MODE_OFF,
    REASON_FORBIDDEN,
    REASON_TIMEOUT,
    VALID_MODES,
    ConsultRequest,
    ConsultResult,
    Teammate,
)


def test_modes_are_the_four_the_spec_names():
    assert VALID_MODES == {"off", "direct", "local", "linear"}
    assert MODE_OFF == "off" and MODE_DIRECT == "direct"


def test_refusal_reasons_are_distinct_strings():
    from src.dispatch import types

    reasons = [v for k, v in vars(types).items() if k.startswith("REASON_")]
    assert len(reasons) == len(set(reasons)), "reason constants must be unique"
    assert all(isinstance(r, str) and r for r in reasons)


def test_granted_result_carries_the_answer():
    r = ConsultResult.granted("72F and sunny", peer="karl-m")

    assert r.ok is True
    assert r.answer == "72F and sunny"
    assert r.reason is None
    assert r.peer == "karl-m"


def test_refused_result_has_no_answer_and_names_a_reason():
    r = ConsultResult.refused(REASON_TIMEOUT, detail="peer took >30s", peer="karl-m")

    assert r.ok is False
    assert r.answer is None
    assert r.reason == REASON_TIMEOUT
    assert "30s" in r.detail


def test_refused_never_fabricates_an_answer_even_when_detail_is_empty():
    r = ConsultResult.refused(REASON_FORBIDDEN)

    assert r.ok is False
    assert r.answer is None
    assert r.detail == ""


def test_consult_request_chain_defaults_empty_and_is_hashable():
    req = ConsultRequest(
        from_agent="billie", session_id="sess-1", to_agent="karl-m", question="hi"
    )

    assert req.chain == ()
    hash(req)  # frozen dataclasses must be hashable — cycle detection uses sets


def test_teammate_records_consult_eligibility():
    t = Teammate("karl-m", "Karl M", "Email", "gpt-5.6-terra", "fast", True)

    assert t.consult_eligible is True
    assert t.speed_class == "fast"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/d/workspaces/jnow/ollie-hermes-orchestrator/.venv/Scripts/python.exe -m pytest tests/test_dispatch_types.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.dispatch'`

- [ ] **Step 3: Write minimal implementation**

Create `src/dispatch/__init__.py` as an empty file.

Create `src/dispatch/types.py`:

```python
"""Shared vocabulary for agent-to-agent dispatch.

`ConsultResult` is deliberately shaped so a refusal cannot carry an answer:
`refused()` never sets `answer`. The failure mode this guards against is an
agent narrating a plausible reply it never received, which is indistinguishable
from a real answer to the human reading the transcript.
"""
from dataclasses import dataclass

MODE_OFF = "off"
MODE_DIRECT = "direct"
MODE_LOCAL = "local"
MODE_LINEAR = "linear"
VALID_MODES = frozenset({MODE_OFF, MODE_DIRECT, MODE_LOCAL, MODE_LINEAR})

REASON_NOT_ENABLED = "not_enabled"
REASON_FORBIDDEN = "forbidden"
REASON_UNKNOWN_PEER = "unknown_peer"
REASON_PEER_NOT_CONSULT_ELIGIBLE = "peer_not_consult_eligible"
REASON_CAP_EXCEEDED = "cap_exceeded"
REASON_TIMEOUT = "timeout"
REASON_PEER_UNAVAILABLE = "peer_unavailable"


@dataclass(frozen=True)
class Teammate:
    agent_id: str
    display_name: str
    subtitle: str | None
    model: str | None
    speed_class: str | None
    consult_eligible: bool


@dataclass(frozen=True)
class ConsultRequest:
    from_agent: str
    session_id: str
    to_agent: str
    question: str
    #: agent ids already in this chain, for cycle detection and the hop cap
    chain: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConsultResult:
    ok: bool
    answer: str | None = None
    reason: str | None = None
    detail: str = ""
    peer: str | None = None

    @classmethod
    def granted(cls, answer: str, peer: str | None = None) -> "ConsultResult":
        return cls(ok=True, answer=answer, peer=peer)

    @classmethod
    def refused(
        cls, reason: str, detail: str = "", peer: str | None = None
    ) -> "ConsultResult":
        return cls(ok=False, answer=None, reason=reason, detail=detail, peer=peer)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/d/workspaces/jnow/ollie-hermes-orchestrator/.venv/Scripts/python.exe -m pytest tests/test_dispatch_types.py -v`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/dispatch/__init__.py src/dispatch/types.py tests/test_dispatch_types.py
git commit -m "feat(dispatch): shared types and refusal vocabulary

ConsultResult.refused() cannot carry an answer. The failure mode that
guards against is an agent narrating a reply it never received, which is
indistinguishable from a real one in the transcript."
```

---

### Task 2: Roster — who can be consulted

**Files:**
- Create: `src/dispatch/roster.py`
- Test: `tests/test_dispatch_roster.py`

**Interfaces:**
- Consumes: `Teammate` from Task 1.
- Produces:
  - `speed_class_for(model: str | None, models: list[dict]) -> str | None`
  - `build_roster(entries: list, models: list[dict], self_agent: str, consult_classes: frozenset[str] = frozenset({"fast"})) -> list[Teammate]`

  `entries` are `src.agents_json.AgentEntry` records (fields used: `id`, `name`, `subtitle`, `model`). `models` is `src.catalog.MODELS`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_dispatch_roster.py`:

```python
from dataclasses import dataclass

from src.dispatch.roster import build_roster, speed_class_for


@dataclass
class _Entry:
    """Stands in for src.agents_json.AgentEntry — only the fields roster reads."""
    id: str
    name: str
    subtitle: str | None = None
    model: str | None = None


MODELS = [
    {"provider": "openai", "id": "gpt-5.6-terra", "label": "Terra", "speed_class": "fast"},
    {"provider": "openai", "id": "gpt-5.6-sol", "label": "Sol", "speed_class": "heavy"},
]


def test_speed_class_looked_up_from_catalog():
    assert speed_class_for("gpt-5.6-terra", MODELS) == "fast"
    assert speed_class_for("gpt-5.6-sol", MODELS) == "heavy"


def test_speed_class_unknown_model_is_none():
    assert speed_class_for("gpt-9.9", MODELS) is None
    assert speed_class_for(None, MODELS) is None


def test_fast_peer_is_consult_eligible():
    roster = build_roster([_Entry("karl-m", "Karl M", "Email", "gpt-5.6-terra")],
                          MODELS, self_agent="billie")

    assert [t.agent_id for t in roster] == ["karl-m"]
    assert roster[0].consult_eligible is True
    assert roster[0].display_name == "Karl M"


def test_heavy_peer_is_listed_but_not_consult_eligible():
    """Heavy peers stay visible — the agent should know they exist and that it
    cannot consult them inline — rather than being hidden."""
    roster = build_roster([_Entry("deep", "Deep", None, "gpt-5.6-sol")],
                          MODELS, self_agent="billie")

    assert roster[0].consult_eligible is False
    assert roster[0].speed_class == "heavy"


def test_unknown_model_is_not_consult_eligible():
    """Fail closed: a model absent from the catalog has no verified speed class."""
    roster = build_roster([_Entry("mystery", "Mystery", None, "gpt-9.9")],
                          MODELS, self_agent="billie")

    assert roster[0].consult_eligible is False


def test_self_is_excluded_from_the_roster():
    roster = build_roster(
        [_Entry("billie", "Billie", None, "gpt-5.6-terra"),
         _Entry("karl-m", "Karl M", None, "gpt-5.6-terra")],
        MODELS, self_agent="billie",
    )

    assert [t.agent_id for t in roster] == ["karl-m"]


def test_consult_classes_is_configurable():
    roster = build_roster([_Entry("deep", "Deep", None, "gpt-5.6-sol")],
                          MODELS, self_agent="billie",
                          consult_classes=frozenset({"fast", "heavy"}))

    assert roster[0].consult_eligible is True


def test_roster_is_sorted_by_agent_id():
    roster = build_roster(
        [_Entry("zed", "Zed", None, "gpt-5.6-terra"),
         _Entry("abe", "Abe", None, "gpt-5.6-terra")],
        MODELS, self_agent="billie",
    )

    assert [t.agent_id for t in roster] == ["abe", "zed"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/d/workspaces/jnow/ollie-hermes-orchestrator/.venv/Scripts/python.exe -m pytest tests/test_dispatch_roster.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.dispatch.roster'`

- [ ] **Step 3: Write minimal implementation**

Create `src/dispatch/roster.py`:

```python
"""Who a given agent may talk to, and which of them can be consulted inline.

Consult eligibility is derived from the model's `speed_class` in src/catalog.py
rather than a separate list, so the weekly catalog-freshness check keeps it from
going stale. A model absent from the catalog has no verified speed class and is
therefore not consult-eligible — fail closed.
"""
from src.dispatch.types import Teammate

DEFAULT_CONSULT_CLASSES = frozenset({"fast"})


def speed_class_for(model: str | None, models: list[dict]) -> str | None:
    """Look up a model's speed_class in the catalog. None when unknown."""
    if not model:
        return None
    for entry in models:
        if entry.get("id") == model:
            return entry.get("speed_class")
    return None


def build_roster(
    entries: list,
    models: list[dict],
    self_agent: str,
    consult_classes: frozenset[str] = DEFAULT_CONSULT_CLASSES,
) -> list[Teammate]:
    """Every other agent on the box, with consult eligibility resolved.

    Heavy peers are listed rather than hidden: the agent should know they exist
    and that it cannot consult them inline, so it can name them to its human
    instead of silently pretending they don't exist.
    """
    roster: list[Teammate] = []
    for entry in entries:
        if entry.id == self_agent:
            continue
        model = getattr(entry, "model", None)
        speed = speed_class_for(model, models)
        roster.append(
            Teammate(
                agent_id=entry.id,
                display_name=getattr(entry, "name", entry.id),
                subtitle=getattr(entry, "subtitle", None),
                model=model,
                speed_class=speed,
                consult_eligible=speed in consult_classes,
            )
        )
    return sorted(roster, key=lambda t: t.agent_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/d/workspaces/jnow/ollie-hermes-orchestrator/.venv/Scripts/python.exe -m pytest tests/test_dispatch_roster.py -v`
Expected: PASS — 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/dispatch/roster.py tests/test_dispatch_roster.py
git commit -m "feat(dispatch): roster with catalog-derived consult eligibility

speed_class comes from src/catalog.py, so the weekly freshness check keeps
it current. A model absent from the catalog is not consult-eligible."
```

---

### Task 3: Authority — provenance and caps

This is the security core of the feature. Read the docstrings before changing anything here.

**Files:**
- Create: `src/dispatch/authority.py`
- Test: `tests/test_dispatch_authority.py`

**Interfaces:**
- Consumes: `ConsultRequest`, `ConsultResult`, and the `REASON_*` constants from Task 1; `Teammate` from Task 2.
- Produces:
  - `Caps(hop_cap: int = 3, fan_out_cap: int = 5)` — frozen dataclass
  - `Origin(user_id: str, tier: str)` — frozen dataclass
  - `resolve_origin(req, owner_lookup, tier_lookup, instance_id) -> Origin | None`
  - `check(req, roster, origin, caps=Caps()) -> ConsultResult | None` — returns a refusal, or `None` meaning "allowed"

  `owner_lookup(agent: str, session_id: str) -> str | None` and `tier_lookup(instance_id: str, user_id: str) -> str` are injected so tests never touch Supabase.

- [ ] **Step 1: Write the failing test**

Create `tests/test_dispatch_authority.py`:

```python
from src.dispatch.authority import Caps, Origin, check, resolve_origin
from src.dispatch.types import (
    REASON_CAP_EXCEEDED,
    REASON_PEER_NOT_CONSULT_ELIGIBLE,
    REASON_UNKNOWN_PEER,
    ConsultRequest,
    Teammate,
)

ROSTER = [
    Teammate("karl-m", "Karl M", "Email", "gpt-5.6-terra", "fast", True),
    Teammate("deep", "Deep", None, "gpt-5.6-sol", "heavy", False),
]
ORIGIN = Origin(user_id="u-1", tier="account_admin")


def _req(**kw):
    base = dict(from_agent="billie", session_id="sess-1", to_agent="karl-m",
                question="q", chain=())
    base.update(kw)
    return ConsultRequest(**base)


def test_origin_resolves_from_the_session_not_the_caller():
    origin = resolve_origin(
        _req(),
        owner_lookup=lambda agent, sid: "u-1" if (agent, sid) == ("billie", "sess-1") else None,
        tier_lookup=lambda inst, uid: "account_admin",
        instance_id="inst-1",
    )

    assert origin == Origin(user_id="u-1", tier="account_admin")


def test_unresolvable_session_returns_none_so_the_caller_fails_closed():
    """The single most important test in this module. A session that does not
    resolve to a human must not produce a permissive default identity."""
    origin = resolve_origin(
        _req(),
        owner_lookup=lambda agent, sid: None,
        tier_lookup=lambda inst, uid: "account_admin",
        instance_id="inst-1",
    )

    assert origin is None


def test_tier_lookup_failure_does_not_invent_an_identity():
    def boom(inst, uid):
        raise RuntimeError("supabase down")

    origin = resolve_origin(
        _req(),
        owner_lookup=lambda agent, sid: "u-1",
        tier_lookup=boom,
        instance_id="inst-1",
    )

    assert origin is None


def test_allowed_request_returns_none():
    assert check(_req(), ROSTER, ORIGIN) is None


def test_unknown_peer_is_refused():
    r = check(_req(to_agent="nobody"), ROSTER, ORIGIN)

    assert r is not None and r.ok is False
    assert r.reason == REASON_UNKNOWN_PEER


def test_heavy_peer_is_refused_with_its_own_reason():
    r = check(_req(to_agent="deep"), ROSTER, ORIGIN)

    assert r.reason == REASON_PEER_NOT_CONSULT_ELIGIBLE
    assert "deep" in r.detail or r.peer == "deep"


def test_self_consult_is_refused():
    r = check(_req(to_agent="billie"), ROSTER, ORIGIN)

    assert r is not None and r.ok is False


def test_cycle_is_refused():
    r = check(_req(chain=("john", "karl-m", "billie")), ROSTER, ORIGIN)

    assert r.reason == REASON_CAP_EXCEEDED
    assert "cycle" in r.detail.lower()


def test_hop_cap_is_refused():
    r = check(_req(chain=("a", "b", "c")), ROSTER, ORIGIN, caps=Caps(hop_cap=3))

    assert r.reason == REASON_CAP_EXCEEDED
    assert "hop" in r.detail.lower()


def test_hop_cap_boundary_allows_exactly_the_cap():
    assert check(_req(chain=("a", "b")), ROSTER, ORIGIN, caps=Caps(hop_cap=3)) is None


def test_empty_question_is_refused():
    r = check(_req(question="   "), ROSTER, ORIGIN)

    assert r is not None and r.ok is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/d/workspaces/jnow/ollie-hermes-orchestrator/.venv/Scripts/python.exe -m pytest tests/test_dispatch_authority.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.dispatch.authority'`

- [ ] **Step 3: Write minimal implementation**

Create `src/dispatch/authority.py`:

```python
"""Provenance resolution and caps — the security core of dispatch.

Two properties this module exists to hold:

1. **Provenance is resolved, not asserted.** The calling agent supplies only its
   own agent id and the session id Hermes gave it. The human is derived from
   `agent_sessions` via the injected `owner_lookup`. An agent claiming "John asked
   for this" is model output and is never accepted as identity.

2. **Fail closed.** Any failure to resolve — unknown session, lookup error,
   tier lookup raising — yields `None`, and the caller must refuse. There is
   deliberately no permissive default: a bug here would let an unattributed
   request run with someone's authority.
"""
import logging
from dataclasses import dataclass

from src.dispatch.types import (
    REASON_CAP_EXCEEDED,
    REASON_FORBIDDEN,
    REASON_PEER_NOT_CONSULT_ELIGIBLE,
    REASON_UNKNOWN_PEER,
    ConsultRequest,
    ConsultResult,
    Teammate,
)

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Caps:
    hop_cap: int = 3
    fan_out_cap: int = 5


@dataclass(frozen=True)
class Origin:
    user_id: str
    tier: str


def resolve_origin(
    req: ConsultRequest,
    owner_lookup,
    tier_lookup,
    instance_id: str,
) -> Origin | None:
    """Derive the originating human from the session. None means refuse."""
    try:
        user_id = owner_lookup(req.from_agent, req.session_id)
    except Exception:
        _logger.warning("dispatch owner_lookup failed", exc_info=True)
        return None
    if not user_id:
        return None
    try:
        tier = tier_lookup(instance_id, user_id)
    except Exception:
        _logger.warning("dispatch tier_lookup failed", exc_info=True)
        return None
    if not tier:
        return None
    return Origin(user_id=user_id, tier=tier)


def check(
    req: ConsultRequest,
    roster: list[Teammate],
    origin: Origin,
    caps: Caps = Caps(),
) -> ConsultResult | None:
    """Return a refusal, or None when the request is allowed."""
    if not req.question.strip():
        return ConsultResult.refused(
            REASON_FORBIDDEN, "question is empty", peer=req.to_agent
        )

    if req.to_agent == req.from_agent:
        return ConsultResult.refused(
            REASON_FORBIDDEN, "an agent cannot consult itself", peer=req.to_agent
        )

    # Cycle before hop cap: a loop is a more specific diagnosis than "too deep".
    if req.to_agent in req.chain:
        return ConsultResult.refused(
            REASON_CAP_EXCEEDED,
            f"cycle — {req.to_agent} is already in this chain",
            peer=req.to_agent,
        )

    if len(req.chain) >= caps.hop_cap:
        return ConsultResult.refused(
            REASON_CAP_EXCEEDED,
            f"hop cap of {caps.hop_cap} reached",
            peer=req.to_agent,
        )

    peer = next((t for t in roster if t.agent_id == req.to_agent), None)
    if peer is None:
        return ConsultResult.refused(
            REASON_UNKNOWN_PEER,
            f"{req.to_agent} is not on this box's roster",
            peer=req.to_agent,
        )

    if not peer.consult_eligible:
        return ConsultResult.refused(
            REASON_PEER_NOT_CONSULT_ELIGIBLE,
            f"{peer.agent_id} runs {peer.model or 'an unknown model'} "
            f"(speed_class={peer.speed_class or 'unknown'}) and cannot be consulted "
            f"inline — name it to your human instead",
            peer=peer.agent_id,
        )

    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/d/workspaces/jnow/ollie-hermes-orchestrator/.venv/Scripts/python.exe -m pytest tests/test_dispatch_authority.py -v`
Expected: PASS — 11 passed

- [ ] **Step 5: Commit**

```bash
git add src/dispatch/authority.py tests/test_dispatch_authority.py
git commit -m "feat(dispatch): provenance resolution and caps

Provenance is derived from agent_sessions, never accepted from the caller.
Every resolution failure returns None so the caller refuses — there is no
permissive default, because a bug here would run an unattributed request
with someone else's authority."
```

---

### Task 4: Audit — every attempt is recorded

**Files:**
- Create: `src/dispatch/audit.py`
- Test: `tests/test_dispatch_audit.py`

**Interfaces:**
- Consumes: `ConsultRequest`, `ConsultResult` from Task 1; `Origin` from Task 3.
- Produces: `record_consult(req, result, origin, instance_id, post) -> None` — never raises. `post(url: str, headers: dict, json: dict) -> None` is injected.

- [ ] **Step 1: Write the failing test**

Create `tests/test_dispatch_audit.py`:

```python
import pytest

from src.dispatch.audit import record_consult
from src.dispatch.authority import Origin
from src.dispatch.types import REASON_TIMEOUT, ConsultRequest, ConsultResult

REQ = ConsultRequest("billie", "sess-1", "karl-m", "does this subject line work?")
ORIGIN = Origin("u-1", "account_admin")


@pytest.fixture(autouse=True)
def _supabase_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://sb.test")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-key")


def _capture():
    calls = []

    def post(url, headers, json):
        calls.append({"url": url, "headers": headers, "json": json})

    return calls, post


def test_granted_consult_is_recorded_as_dispatch_app():
    calls, post = _capture()

    record_consult(REQ, ConsultResult.granted("yes", peer="karl-m"), ORIGIN,
                   "inst-1", post=post)

    assert len(calls) == 1
    body = calls[0]["json"]
    assert body["app"] == "dispatch"
    assert body["event_type"] == "dispatch_consult"
    assert body["status"] == "ok"
    assert body["instance_id"] == "inst-1"
    assert calls[0]["url"].endswith("/rest/v1/governance_events")


def test_refusal_is_recorded_with_the_reason_and_flagged_status():
    calls, post = _capture()

    record_consult(REQ, ConsultResult.refused(REASON_TIMEOUT, "peer took >30s"),
                   ORIGIN, "inst-1", post=post)

    body = calls[0]["json"]
    assert body["status"] == "flagged"
    assert REASON_TIMEOUT in body["content"]


def test_the_question_is_recorded_but_never_the_answer():
    """The audit trail proves who asked whom what. Storing answers would put
    arbitrary model output into an append-only table nobody can redact."""
    calls, post = _capture()

    record_consult(REQ, ConsultResult.granted("SECRET ANSWER", peer="karl-m"),
                   ORIGIN, "inst-1", post=post)

    serialized = str(calls[0]["json"])
    assert "subject line" in serialized
    assert "SECRET ANSWER" not in serialized


def test_chain_is_recorded_for_traceability():
    calls, post = _capture()
    req = ConsultRequest("billie", "s", "karl-m", "q", chain=("john", "billie"))

    record_consult(req, ConsultResult.granted("y"), ORIGIN, "inst-1", post=post)

    assert "billie" in str(calls[0]["json"]["findings"])


def test_a_failing_audit_sink_never_raises():
    """Audit is best-effort at the call site: losing a row must not fail the
    consult the human is waiting on. The loss is logged, not propagated."""
    def boom(url, headers, json):
        raise RuntimeError("supabase down")

    record_consult(REQ, ConsultResult.granted("y"), ORIGIN, "inst-1", post=boom)


def test_missing_supabase_config_is_a_no_op(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    calls, post = _capture()

    record_consult(REQ, ConsultResult.granted("y"), ORIGIN, "inst-1", post=post)

    assert calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/d/workspaces/jnow/ollie-hermes-orchestrator/.venv/Scripts/python.exe -m pytest tests/test_dispatch_audit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.dispatch.audit'`

- [ ] **Step 3: Write minimal implementation**

Create `src/dispatch/audit.py`:

```python
"""Dispatch's writes to the shared governance_events audit trail.

Wire format mirrors src/api/admin.py's existing writer so both produce rows the
same RLS policies and dashboard reader understand.

The answer text is deliberately NOT recorded. The trail's job is to prove who
asked whom what, under whose authority — storing arbitrary model output in an
append-only table nobody can redact is a liability, not an audit improvement.
"""
import logging
import os

from src.dispatch.authority import Origin
from src.dispatch.types import ConsultRequest, ConsultResult

_logger = logging.getLogger(__name__)


def record_consult(
    req: ConsultRequest,
    result: ConsultResult,
    origin: Origin,
    instance_id: str | None,
    post,
) -> None:
    """Append one dispatch_consult row. Best-effort — never raises."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not (url and key):
        return

    content = (
        f"{req.from_agent} -> {req.to_agent}: {req.question}"
        if result.ok
        else f"{req.from_agent} -> {req.to_agent} refused ({result.reason}): "
             f"{result.detail}"
    )

    try:
        post(
            f"{url}/rest/v1/governance_events",
            {
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            {
                "user_email": origin.user_id,
                "user_role": origin.tier,
                "app": "dispatch",
                "event_type": "dispatch_consult",
                "status": "ok" if result.ok else "flagged",
                "title": f"{req.from_agent} -> {req.to_agent}",
                "findings": [{"text": "chain", "chain": list(req.chain)}],
                "content": content,
                "run_id": None,
                "instance_id": instance_id,
            },
        )
    except Exception:
        _logger.warning("dispatch audit write failed", exc_info=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/d/workspaces/jnow/ollie-hermes-orchestrator/.venv/Scripts/python.exe -m pytest tests/test_dispatch_audit.py -v`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/dispatch/audit.py tests/test_dispatch_audit.py
git commit -m "feat(dispatch): governance_events audit writes

Records who asked whom what under whose authority. Deliberately does not
record the answer: arbitrary model output in an append-only table nobody
can redact is a liability, not better auditing."
```

---

### Task 5: Backends — off and direct

**Files:**
- Create: `src/dispatch/backends.py`
- Test: `tests/test_dispatch_backends.py`

**Interfaces:**
- Consumes: `ConsultRequest`, `ConsultResult`, `REASON_*`, `MODE_*` from Task 1.
- Produces:
  - `consult_off(req, peer_port, gateway_key, post=None) -> ConsultResult`
  - `consult_direct(req, peer_port, gateway_key, post, timeout=30.0) -> ConsultResult`
  - `backend_for(mode: str)` — returns the matching callable, raising `ValueError` on an unknown mode

  `post(url: str, headers: dict, json: dict, timeout: float) -> dict` is injected and returns the parsed gateway response.

- [ ] **Step 1: Write the failing test**

Create `tests/test_dispatch_backends.py`:

```python
import pytest

from src.dispatch.backends import backend_for, consult_direct, consult_off
from src.dispatch.types import (
    MODE_DIRECT,
    MODE_OFF,
    REASON_NOT_ENABLED,
    REASON_PEER_UNAVAILABLE,
    REASON_TIMEOUT,
    ConsultRequest,
)

REQ = ConsultRequest("billie", "sess-1", "karl-m", "does this subject line work?")


def _reply(text):
    return {"choices": [{"message": {"content": text}}]}


def test_off_backend_refuses_without_calling_anything():
    calls = []

    r = consult_off(REQ, 8643, "k", post=lambda *a, **kw: calls.append(a))

    assert r.ok is False
    assert r.reason == REASON_NOT_ENABLED
    assert calls == []


def test_direct_backend_returns_the_peer_reply():
    def post(url, headers, json, timeout):
        assert url == "http://127.0.0.1:8643/v1/chat/completions"
        assert headers["Authorization"] == "Bearer gwkey"
        assert REQ.question in json["messages"][0]["content"]
        assert json["stream"] is False
        return _reply("Yes, but shorten it.")

    r = consult_direct(REQ, 8643, "gwkey", post=post)

    assert r.ok is True
    assert r.answer == "Yes, but shorten it."
    assert r.peer == "karl-m"


def test_direct_names_the_asking_agent_so_the_peer_knows_who_is_asking():
    seen = {}

    def post(url, headers, json, timeout):
        seen["content"] = json["messages"][0]["content"]
        return _reply("ok")

    consult_direct(REQ, 8643, "k", post=post)

    assert "billie" in seen["content"]


def test_direct_timeout_is_a_structured_refusal_not_an_exception():
    def post(url, headers, json, timeout):
        raise TimeoutError("read timeout")

    r = consult_direct(REQ, 8643, "k", post=post)

    assert r.ok is False
    assert r.reason == REASON_TIMEOUT
    assert r.answer is None


def test_direct_peer_error_is_a_structured_refusal():
    def post(url, headers, json, timeout):
        raise RuntimeError("connection refused")

    r = consult_direct(REQ, 8643, "k", post=post)

    assert r.ok is False
    assert r.reason == REASON_PEER_UNAVAILABLE
    assert "connection refused" in r.detail


def test_direct_malformed_reply_is_a_refusal_not_a_crash():
    """A gateway that returns an unexpected shape must not fabricate an answer."""
    r = consult_direct(REQ, 8643, "k", post=lambda *a, **kw: {"unexpected": True})

    assert r.ok is False
    assert r.answer is None


def test_direct_empty_reply_is_a_refusal():
    r = consult_direct(REQ, 8643, "k", post=lambda *a, **kw: _reply("   "))

    assert r.ok is False
    assert r.answer is None


def test_backend_for_maps_modes():
    assert backend_for(MODE_OFF) is consult_off
    assert backend_for(MODE_DIRECT) is consult_direct


def test_backend_for_rejects_unknown_and_unimplemented_modes():
    with pytest.raises(ValueError):
        backend_for("local")
    with pytest.raises(ValueError):
        backend_for("nonsense")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/d/workspaces/jnow/ollie-hermes-orchestrator/.venv/Scripts/python.exe -m pytest tests/test_dispatch_backends.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.dispatch.backends'`

- [ ] **Step 3: Write minimal implementation**

Create `src/dispatch/backends.py`:

```python
"""Mode drivers. `direct` owns the only peer-gateway call in the codebase.

Every failure becomes a structured ConsultResult. Nothing here raises to the
caller, because the caller is ultimately a language model: an exception surfaces
to it as an empty tool result, which is exactly the condition under which a model
invents a plausible answer.
"""
import logging

from src.dispatch.types import (
    MODE_DIRECT,
    MODE_OFF,
    REASON_NOT_ENABLED,
    REASON_PEER_UNAVAILABLE,
    REASON_TIMEOUT,
    ConsultRequest,
    ConsultResult,
)

_logger = logging.getLogger(__name__)

_PROMPT = (
    "You are being consulted by a teammate agent, {frm}, on behalf of its human. "
    "Answer directly and concisely from your own expertise. If the question is "
    "outside what you know, say so plainly rather than guessing.\n\n"
    "Question from {frm}:\n{question}"
)


def consult_off(req: ConsultRequest, peer_port: int, gateway_key: str, post=None
                ) -> ConsultResult:
    """Refuse without touching the network. Never calls `post`."""
    return ConsultResult.refused(
        REASON_NOT_ENABLED,
        "dispatch is disabled on this instance (DISPATCH_MODE=off)",
        peer=req.to_agent,
    )


def consult_direct(
    req: ConsultRequest,
    peer_port: int,
    gateway_key: str,
    post,
    timeout: float = 30.0,
) -> ConsultResult:
    """Ask the peer's gateway synchronously and return its reply."""
    url = f"http://127.0.0.1:{peer_port}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {gateway_key}",
        "Content-Type": "application/json",
    }
    body = {
        "messages": [
            {
                "role": "user",
                "content": _PROMPT.format(frm=req.from_agent, question=req.question),
            }
        ],
        "stream": False,
    }

    try:
        data = post(url, headers, body, timeout)
    except TimeoutError as exc:
        return ConsultResult.refused(
            REASON_TIMEOUT, f"{req.to_agent} did not answer in {timeout:g}s: {exc}",
            peer=req.to_agent,
        )
    except Exception as exc:  # noqa: BLE001 — every failure is a structured refusal
        return ConsultResult.refused(
            REASON_PEER_UNAVAILABLE, f"{exc}", peer=req.to_agent
        )

    try:
        answer = data["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001 — unexpected shape is a refusal, not a crash
        return ConsultResult.refused(
            REASON_PEER_UNAVAILABLE,
            "peer returned an unrecognised response shape",
            peer=req.to_agent,
        )

    if not isinstance(answer, str) or not answer.strip():
        return ConsultResult.refused(
            REASON_PEER_UNAVAILABLE, "peer returned an empty answer",
            peer=req.to_agent,
        )

    return ConsultResult.granted(answer, peer=req.to_agent)


_BACKENDS = {MODE_OFF: consult_off, MODE_DIRECT: consult_direct}


def backend_for(mode: str):
    """Driver for `mode`. `local` and `linear` are not implemented in this slice."""
    try:
        return _BACKENDS[mode]
    except KeyError:
        raise ValueError(f"dispatch mode not available in this build: {mode!r}") from None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/d/workspaces/jnow/ollie-hermes-orchestrator/.venv/Scripts/python.exe -m pytest tests/test_dispatch_backends.py -v`
Expected: PASS — 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/dispatch/backends.py tests/test_dispatch_backends.py
git commit -m "feat(dispatch): off and direct backends

Nothing here raises. An exception reaches a language model as an empty
tool result, which is exactly when a model invents a plausible answer, so
every failure becomes a structured refusal instead."
```

---

### Task 6: The orchestrator API

**Files:**
- Create: `src/api/dispatch.py`
- Modify: `src/api/main.py` (import + `include_router`, alongside the existing routers at lines 55–66)
- Test: `tests/test_api_dispatch.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5; `read_agents` from `src.agents_json`; `get_session_owner` from `src.api.sessions`; `resolve_tier` from `src.api.roles`; `require_bearer` from `src.auth`; `MODELS` from `src.catalog`.
- Produces: `router` — `GET /v1/dispatch/teammates`, `POST /v1/dispatch/consult`.

**Existing conventions this task must follow** (copy them, do not invent):
- `router = APIRouter(tags=["dispatch"], dependencies=[Depends(require_bearer)])` — the same shape as `src/api/sessions.py:25`.
- Config is read off `request.app.state.config` (e.g. `.instance_id`), as `src/api/admin.py` does.
- Tests build the app with `TestClient(create_app())` using the `fake_env` fixture and send `headers={"Authorization": "Bearer topsecret"}` — see `tests/test_api_catalog.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_dispatch.py`:

```python
import pytest
from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer topsecret"}


@pytest.fixture
def client(fake_env):
    from src.api.main import create_app

    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """No test in this module may reach a gateway or Supabase."""
    monkeypatch.setenv("DISPATCH_MODE", "direct")
    monkeypatch.setattr("src.api.dispatch.get_session_owner", lambda a, s: "u-1")
    monkeypatch.setattr("src.api.dispatch.resolve_tier", lambda i, u: "account_admin")
    monkeypatch.setattr("src.api.dispatch.record_consult",
                        lambda *a, **kw: None)


def test_teammates_requires_auth(client):
    assert client.get("/v1/dispatch/teammates?agent=billie").status_code in (401, 403)


def test_teammates_lists_peers_with_eligibility(client, monkeypatch):
    from src.dispatch.types import Teammate

    monkeypatch.setattr(
        "src.api.dispatch.build_roster",
        lambda *a, **kw: [Teammate("karl-m", "Karl M", "Email", "gpt-5.6-terra",
                                   "fast", True)],
    )

    r = client.get("/v1/dispatch/teammates?agent=billie", headers=AUTH)

    assert r.status_code == 200
    body = r.json()["teammates"]
    assert body[0]["agent_id"] == "karl-m"
    assert body[0]["consult_eligible"] is True


def test_consult_returns_the_peer_answer(client, monkeypatch):
    from src.dispatch.types import ConsultResult, Teammate

    monkeypatch.setattr(
        "src.api.dispatch.build_roster",
        lambda *a, **kw: [Teammate("karl-m", "K", None, "gpt-5.6-terra", "fast", True)],
    )
    monkeypatch.setattr("src.api.dispatch.port_for", lambda agent: 8643)
    monkeypatch.setattr(
        "src.api.dispatch.backend_for",
        lambda mode: (lambda req, port, key, post, **kw:
                      ConsultResult.granted("shorten it", peer=req.to_agent)),
    )

    r = client.post("/v1/dispatch/consult", headers=AUTH, json={
        "from_agent": "billie", "session_id": "s1",
        "to_agent": "karl-m", "question": "subject line ok?",
    })

    assert r.status_code == 200
    assert r.json() == {"ok": True, "answer": "shorten it", "reason": None,
                        "detail": "", "peer": "karl-m"}


def test_unresolvable_session_is_refused_and_never_reaches_a_backend(client, monkeypatch):
    """Fail-closed at the API boundary: no provenance, no dispatch."""
    called = []
    monkeypatch.setattr("src.api.dispatch.get_session_owner", lambda a, s: None)
    monkeypatch.setattr("src.api.dispatch.backend_for",
                        lambda mode: called.append(mode))

    r = client.post("/v1/dispatch/consult", headers=AUTH, json={
        "from_agent": "billie", "session_id": "bogus",
        "to_agent": "karl-m", "question": "q",
    })

    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert r.json()["reason"] == "forbidden"
    assert called == []


def test_caller_supplied_identity_is_ignored(client, monkeypatch):
    """A caller cannot assert who it acts for — the field is not even read."""
    from src.dispatch.types import ConsultResult, Teammate

    monkeypatch.setattr(
        "src.api.dispatch.build_roster",
        lambda *a, **kw: [Teammate("karl-m", "K", None, "gpt-5.6-terra", "fast", True)],
    )
    monkeypatch.setattr("src.api.dispatch.port_for", lambda agent: 8643)
    monkeypatch.setattr(
        "src.api.dispatch.backend_for",
        lambda mode: (lambda req, port, key, post, **kw:
                      ConsultResult.granted("ok", peer=req.to_agent)),
    )

    r = client.post("/v1/dispatch/consult", headers=AUTH, json={
        "from_agent": "billie", "session_id": "s1", "to_agent": "karl-m",
        "question": "q", "user_id": "someone-else", "tier": "account_admin",
    })

    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_mode_off_refuses_without_a_backend_call(client, monkeypatch):
    monkeypatch.setenv("DISPATCH_MODE", "off")

    r = client.post("/v1/dispatch/consult", headers=AUTH, json={
        "from_agent": "billie", "session_id": "s1",
        "to_agent": "karl-m", "question": "q",
    })

    assert r.json()["ok"] is False
    assert r.json()["reason"] == "not_enabled"


def test_unknown_peer_is_refused(client, monkeypatch):
    monkeypatch.setattr("src.api.dispatch.build_roster", lambda *a, **kw: [])

    r = client.post("/v1/dispatch/consult", headers=AUTH, json={
        "from_agent": "billie", "session_id": "s1",
        "to_agent": "ghost", "question": "q",
    })

    assert r.json()["reason"] == "unknown_peer"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/d/workspaces/jnow/ollie-hermes-orchestrator/.venv/Scripts/python.exe -m pytest tests/test_api_dispatch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.api.dispatch'`

- [ ] **Step 3: Write minimal implementation**

Create `src/api/dispatch.py`:

```python
"""Dispatch API — the mediator between a calling agent and its peer.

The plugin never reaches a peer gateway; it calls here. That is what makes
provenance resolvable rather than asserted: this module derives the human from
`agent_sessions` and the caller has no way to influence the answer.
"""
import logging
import os

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from src.agents_json import read_agents
from src.api.roles import resolve_tier
from src.api.sessions import get_session_owner
from src.auth import require_bearer
from src.catalog import MODELS
from src.dispatch.audit import record_consult
from src.dispatch.authority import Caps, check, resolve_origin
from src.dispatch.backends import backend_for
from src.dispatch.roster import build_roster
from src.dispatch.types import (
    MODE_OFF,
    REASON_FORBIDDEN,
    VALID_MODES,
    ConsultRequest,
    ConsultResult,
)

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["dispatch"], dependencies=[Depends(require_bearer)])

_GATEWAY_TIMEOUT = 30.0


class ConsultBody(BaseModel):
    from_agent: str
    session_id: str
    to_agent: str
    question: str
    chain: list[str] = []
    # Any identity fields a caller sends are deliberately absent from this model:
    # pydantic drops unknown keys, so a caller cannot assert who it acts for.


def current_mode() -> str:
    mode = os.environ.get("DISPATCH_MODE", MODE_OFF).strip() or MODE_OFF
    return mode if mode in VALID_MODES else MODE_OFF


def _env_path(cfg) -> str:
    return os.path.join(os.environ.get("HERMES_STACK_DIR", ""), ".env")


def port_for(agent: str) -> int | None:
    for entry in read_agents(_env_path(None)):
        if entry.id == agent:
            return entry.gateway_port
    return None


def _gateway_key() -> str:
    return os.environ.get("HERMES_GATEWAY_KEY", "")


def _post(url, headers, json, timeout):
    resp = httpx.post(url, headers=headers, json=json, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _audit_post(url, headers, json):
    httpx.post(url, headers=headers, json=json, timeout=10.0).raise_for_status()


def _roster_for(agent: str):
    return build_roster(read_agents(_env_path(None)), MODELS, self_agent=agent)


@router.get("/v1/dispatch/teammates")
def teammates(agent: str):
    roster = _roster_for(agent)
    return {
        "mode": current_mode(),
        "teammates": [
            {
                "agent_id": t.agent_id,
                "display_name": t.display_name,
                "subtitle": t.subtitle,
                "speed_class": t.speed_class,
                "consult_eligible": t.consult_eligible,
            }
            for t in roster
        ],
    }


@router.post("/v1/dispatch/consult")
def consult(body: ConsultBody, request: Request):
    req = ConsultRequest(
        from_agent=body.from_agent,
        session_id=body.session_id,
        to_agent=body.to_agent,
        question=body.question,
        chain=tuple(body.chain),
    )

    try:
        instance_id = request.app.state.config.instance_id
    except Exception:
        instance_id = None

    origin = resolve_origin(
        req,
        owner_lookup=get_session_owner,
        tier_lookup=resolve_tier,
        instance_id=instance_id or "",
    )
    if origin is None:
        # Fail closed. No provenance, no dispatch, and no backend is consulted.
        return ConsultResult.refused(
            REASON_FORBIDDEN,
            "could not establish which human this request acts for",
            peer=req.to_agent,
        ).__dict__

    refusal = check(req, _roster_for(req.from_agent), origin, caps=Caps())
    if refusal is not None:
        record_consult(req, refusal, origin, instance_id, post=_audit_post)
        return refusal.__dict__

    port = port_for(req.to_agent)
    if port is None:
        refusal = ConsultResult.refused(
            REASON_FORBIDDEN, "peer has no gateway port", peer=req.to_agent
        )
        record_consult(req, refusal, origin, instance_id, post=_audit_post)
        return refusal.__dict__

    backend = backend_for(current_mode())
    result = backend(req, port, _gateway_key(), post=_post,
                     timeout=_GATEWAY_TIMEOUT) \
        if current_mode() != MODE_OFF else backend(req, port, _gateway_key())

    record_consult(req, result, origin, instance_id, post=_audit_post)
    return result.__dict__
```

Then register it in `src/api/main.py`. Add the import alongside the other router imports, and the `include_router` call after `sessions_router` (line 66):

```python
from src.api.dispatch import router as dispatch_router
...
    app.include_router(dispatch_router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/d/workspaces/jnow/ollie-hermes-orchestrator/.venv/Scripts/python.exe -m pytest tests/test_api_dispatch.py -v`
Expected: PASS — 7 passed

- [ ] **Step 5: Run the full suite for regressions**

Run: `/d/workspaces/jnow/ollie-hermes-orchestrator/.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: the pre-task baseline plus your new tests. Paste the literal line pytest printed.

- [ ] **Step 6: Commit**

```bash
git add src/api/dispatch.py src/api/main.py tests/test_api_dispatch.py
git commit -m "feat(dispatch): orchestrator mediator API

GET /v1/dispatch/teammates and POST /v1/dispatch/consult. The request
model has no identity fields, so a caller cannot assert who it acts for;
the human is derived from agent_sessions and an unresolvable session is
refused before any backend is reached."
```

---

### Task 7: The Hermes tool plugin

**Files:**
- Create: `plugins/dispatch/plugin.yaml`, `plugins/dispatch/__init__.py`, `plugins/dispatch/provider.py`, `plugins/dispatch/http_client.py`
- Test: `tests/test_plugin_dispatch.py`

**Interfaces:**
- Consumes: the orchestrator API from Task 6 (over HTTP).
- Produces: `DispatchProvider` with `name`, `is_available()`, `initialize(session_id, **kwargs)`, `get_config_schema()`, `system_prompt_block()`, `get_tool_schemas()`, `handle_tool_call(name, args)`.

**Reference implementation to copy the shape from:** `ollie-hermes-cortex/plugins/memory/cortex/provider.py` — same four hooks, same `initialize(session_id, **kwargs)` signature, same guarded `from hermes_cli.config import load_config` import so the module is importable outside Hermes.

- [ ] **Step 1: Write the failing test**

Create `tests/test_plugin_dispatch.py`:

```python
import json

import pytest

from plugins.dispatch.provider import DispatchProvider


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_URL", "http://127.0.0.1:9123")
    monkeypatch.setenv("ORCHESTRATOR_KEY", "topsecret")
    monkeypatch.setenv("DISPATCH_AGENT_ID", "billie")
    p = DispatchProvider()
    p.initialize("sess-1")
    return p


def test_off_mode_exposes_no_tools_and_no_prompt_block(provider, monkeypatch):
    monkeypatch.setenv("DISPATCH_MODE", "off")

    assert provider.get_tool_schemas() == []
    assert provider.system_prompt_block() == ""


def test_direct_mode_exposes_exactly_the_two_tools(provider, monkeypatch):
    monkeypatch.setenv("DISPATCH_MODE", "direct")

    names = sorted(t["name"] for t in provider.get_tool_schemas())

    assert names == ["ask_teammate", "list_teammates"]


def test_prompt_block_forbids_fabricating_and_claiming_handoff(provider, monkeypatch):
    monkeypatch.setenv("DISPATCH_MODE", "direct")

    block = provider.system_prompt_block().lower()

    assert "never" in block
    assert "invent" in block or "fabricate" in block
    assert "handed" in block or "assigned" in block


def test_initialize_captures_the_session_id(provider):
    assert provider._session_id == "sess-1"


def test_ask_teammate_sends_agent_and_session_and_returns_the_answer(provider, monkeypatch):
    monkeypatch.setenv("DISPATCH_MODE", "direct")
    sent = {}

    def fake_post(path, payload):
        sent["path"] = path
        sent["payload"] = payload
        return {"ok": True, "answer": "shorten it", "reason": None,
                "detail": "", "peer": "karl-m"}

    monkeypatch.setattr(provider._client, "post", fake_post)

    out = provider.handle_tool_call(
        "ask_teammate", {"teammate": "karl-m", "question": "subject ok?"}
    )

    assert sent["payload"]["from_agent"] == "billie"
    assert sent["payload"]["session_id"] == "sess-1"
    assert "shorten it" in out


def test_refusal_is_surfaced_verbatim_and_never_as_an_answer(provider, monkeypatch):
    monkeypatch.setenv("DISPATCH_MODE", "direct")
    monkeypatch.setattr(
        provider._client, "post",
        lambda path, payload: {"ok": False, "answer": None, "reason": "timeout",
                               "detail": "karl-m did not answer in 30s",
                               "peer": "karl-m"},
    )

    out = provider.handle_tool_call("ask_teammate", {"teammate": "karl-m",
                                                    "question": "q"})

    assert "timeout" in out
    assert "did not answer" in out


def test_transport_failure_becomes_a_structured_refusal_not_an_exception(provider, monkeypatch):
    monkeypatch.setenv("DISPATCH_MODE", "direct")

    def boom(path, payload):
        raise RuntimeError("orchestrator unreachable")

    monkeypatch.setattr(provider._client, "post", boom)

    out = provider.handle_tool_call("ask_teammate", {"teammate": "karl-m",
                                                    "question": "q"})

    assert "orchestrator unreachable" in out
    payload = json.loads(out) if out.strip().startswith("{") else {"raw": out}
    assert "ok" not in payload or payload.get("ok") is False


def test_unknown_tool_name_is_reported_not_raised(provider):
    assert "unknown" in provider.handle_tool_call("nope", {}).lower()


def test_config_schema_exposes_mode_and_orchestrator_url(provider):
    keys = {f["key"] for f in provider.get_config_schema()}

    assert "mode" in keys
    assert "orchestrator_url" in keys
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/d/workspaces/jnow/ollie-hermes-orchestrator/.venv/Scripts/python.exe -m pytest tests/test_plugin_dispatch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'plugins.dispatch'`

- [ ] **Step 3: Write minimal implementation**

Create `plugins/dispatch/plugin.yaml`:

```yaml
name: dispatch
version: "1.0.0"
description: "Agent-to-agent consult for Hermes agents on an Ollie box"
author: "JNOW"
min_hermes_version: "0.13.0"
```

Create `plugins/dispatch/__init__.py`:

```python
from plugins.dispatch.provider import DispatchProvider

__all__ = ["DispatchProvider"]
```

Create `plugins/dispatch/http_client.py`:

```python
"""Thin client for the orchestrator's dispatch API. The only I/O in the plugin."""
import os

import httpx

_TIMEOUT = 35.0  # must exceed the orchestrator's own 30s gateway timeout


class DispatchHttpClient:
    def __init__(self, base_url: str | None = None, key: str | None = None):
        self._base = (base_url or os.environ.get("ORCHESTRATOR_URL")
                      or "http://127.0.0.1:9123").rstrip("/")
        self._key = key or os.environ.get("ORCHESTRATOR_KEY", "")

    def post(self, path: str, payload: dict) -> dict:
        resp = httpx.post(
            f"{self._base}{path}",
            headers={"Authorization": f"Bearer {self._key}",
                     "Content-Type": "application/json"},
            json=payload,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def get(self, path: str, params: dict) -> dict:
        resp = httpx.get(
            f"{self._base}{path}",
            headers={"Authorization": f"Bearer {self._key}"},
            params=params,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
```

Create `plugins/dispatch/provider.py`:

```python
"""Hermes tool plugin exposing agent-to-agent consult.

Installed with `hermes plugins install` — the supported, upgrade-safe path for
tool plugins. It deliberately does NOT go in hermes-agent's bundled tree, which
`hermes update` wipes.

In DISPATCH_MODE=off this plugin contributes no tool schemas and no system-prompt
text, so an agent's context is byte-identical to a box without it installed.
"""
import json
import os

from plugins.dispatch.http_client import DispatchHttpClient

_PROMPT_BLOCK = """\
You can consult teammate agents on this box.

- `list_teammates` shows who is available and whether each can be consulted inline.
- `ask_teammate` asks one of them a question and returns their answer.

Rules that are not negotiable:
- NEVER invent or paraphrase a teammate's answer. If the tool returns a refusal,
  say what it says. An answer you did not receive is indistinguishable from one
  you did, to the person reading your reply.
- NEVER say work was handed off, assigned, or sent to a teammate. This tool only
  asks questions and returns answers; it cannot give anyone work.
- If a teammate cannot be consulted inline, name them and the exact ask to your
  human instead.
"""


class DispatchProvider:
    def __init__(self):
        self._session_id = ""
        self._client = DispatchHttpClient()

    @property
    def name(self) -> str:
        return "dispatch"

    def is_available(self) -> bool:
        return self._mode() != "off"

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id

    @staticmethod
    def _mode() -> str:
        return os.environ.get("DISPATCH_MODE", "off").strip() or "off"

    @staticmethod
    def _agent_id() -> str:
        return os.environ.get("DISPATCH_AGENT_ID", "")

    def get_config_schema(self) -> list[dict]:
        return [
            {"key": "mode", "label": "Dispatch mode",
             "type": "select", "options": ["off", "direct"], "default": "off"},
            {"key": "orchestrator_url", "label": "Orchestrator URL",
             "type": "string", "default": "http://127.0.0.1:9123"},
        ]

    def system_prompt_block(self) -> str:
        return "" if self._mode() == "off" else _PROMPT_BLOCK

    def get_tool_schemas(self) -> list[dict]:
        if self._mode() == "off":
            return []
        return [
            {
                "name": "list_teammates",
                "description": (
                    "List the other agents on this box and whether each can be "
                    "consulted inline."
                ),
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "ask_teammate",
                "description": (
                    "Ask one teammate agent a question and get their answer in "
                    "this turn. Use for questions, not for giving work."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "teammate": {"type": "string",
                                     "description": "agent_id from list_teammates"},
                        "question": {"type": "string"},
                    },
                    "required": ["teammate", "question"],
                },
            },
        ]

    def handle_tool_call(self, name: str, args: dict) -> str:
        if name == "list_teammates":
            return self._list_teammates()
        if name == "ask_teammate":
            return self._ask_teammate(args)
        return f"Unknown dispatch tool: {name}"

    def _list_teammates(self) -> str:
        try:
            data = self._client.get("/v1/dispatch/teammates",
                                    {"agent": self._agent_id()})
        except Exception as exc:  # noqa: BLE001 — never raise into the model
            return json.dumps({"ok": False, "reason": "orchestrator_unreachable",
                               "detail": str(exc)})
        return json.dumps(data)

    def _ask_teammate(self, args: dict) -> str:
        payload = {
            "from_agent": self._agent_id(),
            "session_id": self._session_id,
            "to_agent": args.get("teammate", ""),
            "question": args.get("question", ""),
            "chain": [],
        }
        try:
            data = self._client.post("/v1/dispatch/consult", payload)
        except Exception as exc:  # noqa: BLE001 — never raise into the model
            return json.dumps({"ok": False, "reason": "orchestrator_unreachable",
                               "detail": str(exc)})
        return json.dumps(data)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/d/workspaces/jnow/ollie-hermes-orchestrator/.venv/Scripts/python.exe -m pytest tests/test_plugin_dispatch.py -v`
Expected: PASS — 9 passed

- [ ] **Step 5: Run the full suite**

Run: `/d/workspaces/jnow/ollie-hermes-orchestrator/.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: baseline plus your new tests. Paste the literal line pytest printed.

- [ ] **Step 6: Commit**

```bash
git add plugins/dispatch tests/test_plugin_dispatch.py
git commit -m "feat(dispatch): Hermes tool plugin

Installs via `hermes plugins install`, the supported upgrade-safe path for
tool plugins. In DISPATCH_MODE=off it contributes no tool schemas and no
prompt text, so an agent's context is identical to a box without it.

The prompt block forbids inventing a teammate's answer and forbids
claiming work was handed off -- this tool asks questions, it cannot give
anyone work."
```

---

### Task 8: `off` is provably inert, and the operator runbook

**Files:**
- Create: `docs/runbooks/agent-dispatch.md`
- Test: `tests/test_dispatch_off_is_inert.py`

**Interfaces:**
- Consumes: `DispatchProvider` from Task 7; the API from Task 6.

- [ ] **Step 1: Write the failing test**

Create `tests/test_dispatch_off_is_inert.py`:

```python
import pytest
from fastapi.testclient import TestClient

from plugins.dispatch.provider import DispatchProvider

AUTH = {"Authorization": "Bearer topsecret"}


@pytest.fixture(autouse=True)
def _off(monkeypatch):
    monkeypatch.setenv("DISPATCH_MODE", "off")
    monkeypatch.setenv("DISPATCH_AGENT_ID", "billie")


def test_plugin_contributes_nothing_to_the_model_context():
    """The acceptance test for existing customer boxes: with mode off, an agent's
    tool list and system prompt must be identical to a box without the plugin."""
    p = DispatchProvider()
    p.initialize("sess-1")

    assert p.get_tool_schemas() == []
    assert p.system_prompt_block() == ""
    assert p.is_available() is False


def test_unset_mode_defaults_to_off(monkeypatch):
    monkeypatch.delenv("DISPATCH_MODE", raising=False)
    p = DispatchProvider()

    assert p.get_tool_schemas() == []
    assert p.system_prompt_block() == ""


def test_unrecognised_mode_falls_back_to_off_at_the_api(monkeypatch, fake_env):
    from src.api.dispatch import current_mode

    monkeypatch.setenv("DISPATCH_MODE", "banana")

    assert current_mode() == "off"


def test_consult_in_off_mode_never_reaches_a_gateway(monkeypatch, fake_env):
    from src.api.main import create_app

    monkeypatch.setattr("src.api.dispatch.get_session_owner", lambda a, s: "u-1")
    monkeypatch.setattr("src.api.dispatch.resolve_tier", lambda i, u: "account_admin")
    monkeypatch.setattr("src.api.dispatch.record_consult", lambda *a, **kw: None)

    def explode(*a, **kw):
        raise AssertionError("off mode must not call a gateway")

    monkeypatch.setattr("src.api.dispatch._post", explode)

    r = TestClient(create_app()).post("/v1/dispatch/consult", headers=AUTH, json={
        "from_agent": "billie", "session_id": "s1",
        "to_agent": "karl-m", "question": "q",
    })

    assert r.json()["ok"] is False
    assert r.json()["reason"] == "not_enabled"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/d/workspaces/jnow/ollie-hermes-orchestrator/.venv/Scripts/python.exe -m pytest tests/test_dispatch_off_is_inert.py -v`
Expected: FAIL — the `off`-mode consult path is not yet wired to short-circuit before `port_for`, so the last test fails on a missing peer rather than `not_enabled`.

- [ ] **Step 3: Make `off` short-circuit before any peer lookup**

In `src/api/dispatch.py`'s `consult()`, move the mode check ahead of the port lookup. Replace the block from `port = port_for(...)` through the `backend(...)` call with:

```python
    mode = current_mode()
    if mode == MODE_OFF:
        refusal = ConsultResult.refused(
            REASON_NOT_ENABLED,
            "dispatch is disabled on this instance (DISPATCH_MODE=off)",
            peer=req.to_agent,
        )
        record_consult(req, refusal, origin, instance_id, post=_audit_post)
        return refusal.__dict__

    port = port_for(req.to_agent)
    if port is None:
        refusal = ConsultResult.refused(
            REASON_FORBIDDEN, "peer has no gateway port", peer=req.to_agent
        )
        record_consult(req, refusal, origin, instance_id, post=_audit_post)
        return refusal.__dict__

    result = backend_for(mode)(req, port, _gateway_key(), post=_post,
                               timeout=_GATEWAY_TIMEOUT)
    record_consult(req, result, origin, instance_id, post=_audit_post)
    return result.__dict__
```

Add `REASON_NOT_ENABLED` to the imports from `src.dispatch.types`.

- [ ] **Step 4: Run test to verify it passes**

Run: `/d/workspaces/jnow/ollie-hermes-orchestrator/.venv/Scripts/python.exe -m pytest tests/test_dispatch_off_is_inert.py tests/test_api_dispatch.py -v`
Expected: PASS — both files

- [ ] **Step 5: Write the runbook**

Create `docs/runbooks/agent-dispatch.md`. Match the tone of the existing runbooks in that directory. Cover:

- What it does: one agent asks another a question and gets the answer in the same turn. It cannot give anyone work — that is slice 3.
- **Enabling it:** set `DISPATCH_MODE=direct` and `DISPATCH_AGENT_ID=<agent>` in the profile's environment, install the plugin with `hermes plugins install`, restart that profile's gateway. Default is `off`, and `off` contributes no tools and no prompt text.
- **Re-install after `hermes update`** if a check shows the plugin missing — tool plugins are expected to survive, but this has not yet been observed across an update on a live box.
- The refusal reasons and what each means: `not_enabled`, `forbidden`, `unknown_peer`, `peer_not_consult_eligible`, `cap_exceeded`, `timeout`, `peer_unavailable`.
- **Consult eligibility comes from the model catalog.** Only `speed_class: fast` peers can be consulted inline; `heavy` peers are listed but refused with `peer_not_consult_eligible`. Changing a peer's model changes its eligibility.
- **Provenance is fail-closed.** If the session id the plugin holds does not match `agent_sessions.hermes_session_id`, every consult is refused with `forbidden`. That is the expected symptom of the open spike in the spec — if every consult refuses with `forbidden` on a freshly enabled box, this is the first thing to check.
- Where the audit trail lands: `governance_events`, `app='dispatch'`, one row per attempt including refusals. Answers are not recorded, by design.

- [ ] **Step 6: Run the full suite**

Run: `/d/workspaces/jnow/ollie-hermes-orchestrator/.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: baseline plus all new tests. Paste the literal line pytest printed.

- [ ] **Step 7: Commit**

```bash
git add src/api/dispatch.py docs/runbooks/agent-dispatch.md tests/test_dispatch_off_is_inert.py
git commit -m "feat(dispatch): off short-circuits before peer lookup; add runbook

off mode now refuses before any roster or port lookup, so no code path can
reach a gateway when dispatch is disabled. Runbook documents the
fail-closed provenance symptom: if every consult refuses with 'forbidden'
on a freshly enabled box, the plugin's session id does not match
agent_sessions -- which is the open spike in the spec."
```

---

## Deferred to later slices

Not in scope here, listed so nobody implements them by accident:

- `dispatch_tasks` migration, the queue, `assign_task`, `check_assignments` (slice 3)
- The orchestrator-owned heartbeat systemd timer and stale sweep (slice 3)
- The per-agent `task_class` accept-list — unreachable while `assign` is disabled (slice 3)
- Dashboard queue view and approve/reject UI (slice 4)
- The Linear/Open Engine adapter (slice 5)
- Fan-out cap enforcement — `Caps.fan_out_cap` exists but only constrains concurrent *tasks*, which do not exist until slice 3
- Per-chain token budget, and streaming "asking Karl…" progress

## Open item this plan does not close

**The provenance spike (spec Risk 1).** Nothing here proves the `session_id` Hermes hands the plugin equals `agent_sessions.hermes_session_id`. The design is fail-closed, so a mismatch refuses every consult with `forbidden` rather than doing anything unsafe — and the runbook says so — but the first enablement on a real box is still the moment of truth. Do not treat a green suite as evidence that provenance resolves on a live box.

---

## Self-review

**Spec coverage (slices 1–2 only):**

| Spec section | Task |
|---|---|
| §1 orchestrator mediates, never peer-to-peer | Task 6 (plugin has no gateway URL at all) |
| §3 provenance resolved not asserted; fail closed | Tasks 3, 6 |
| §3 authority never escalates | Task 3 (`Origin` carries the human's tier; nothing raises it) |
| §4 `off` genuinely inert | Tasks 7, 8 |
| §4 `direct` consult | Tasks 5, 6, 7 |
| §6 cheap-peer rule from `speed_class` | Task 2 |
| §6 caps: hop, cycle | Task 3 |
| §6 structured failure contract, never fabricate | Tasks 1, 5, 7 |
| §7 config surface | Task 7 (`get_config_schema`) |
| governance_events audit | Task 4 |
| Zero upstream patches | Task 7 (`hermes plugins install`) |
| Runbook | Task 8 |

Deliberately not covered, with reasons in "Deferred": the queue, heartbeat, accept-list, dashboard, Linear adapter, fan-out enforcement, token budget. `long_context_threshold`-based conversion is also deferred — it needs the caller's context size, which `direct` does not carry; the spec's cost bound is therefore only partially realised in this slice, and that is called out here rather than silently dropped.

**Placeholder scan:** no TBDs. Task 8 Step 5 describes runbook contents rather than dictating prose, which is deliberate — it lists every required point.

**Type consistency:** `ConsultRequest`/`ConsultResult`/`Teammate`/`Origin`/`Caps` field names are consistent across Tasks 1–8. `build_roster(entries, models, self_agent, consult_classes)` matches its call in Task 6. `backend_for(mode)` returns a callable taking `(req, port, key, post, timeout=...)`; `consult_off` accepts `post=None` and ignores it, and after Task 8 `off` never reaches the backend call at all. `record_consult(req, result, origin, instance_id, post)` matches every call site.
