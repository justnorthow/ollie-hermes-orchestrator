# TRAIGA Sub-project C (v1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Two deterministic, server-side gates in the Ollie run-proxy — a pre-run prohibited-use refusal and a post-run compliance-attestation gate — grounded in A's register, audited via `governance_events`, deployed to the sandbox box in observe mode.

**Architecture:** Pure `guardrail.py` (screen + attestation logic, non-LLM) consumed by `runs.py` (`create_run` pre-run, `run_events` post-run). Skills in `jnow-workspace` emit the attestation. Spec: `docs/superpowers/specs/2026-06-29-traiga-guardrails-c-design.md`.

**Tech Stack:** Python 3 / FastAPI (orchestrator); pytest. Two repos:
- **ORCH** = `D:/devprojects/ollie-hermes-orchestrator` (branch `traiga-guardrails-c`). Its own venv/pytest.
- **JW** = `jnow-workspace` skills (a worktree off `main` — create `traiga-c-attestation` off main; do NOT use the loops/site worktrees).

## Global Constraints

- Gates are **deterministic / non-LLM** — no model calls in the proxy.
- **Non-governed runs stream unchanged**; only governed runs (carrying `X-Gov-App`/`X-Gov-Event-Type`) are buffered for Gate 2.
- **Observe by default:** `GUARDRAIL_ENFORCE_APPS` (env, comma-sep app ids) lists apps in enforce mode; absent ⇒ observe (record, never withhold).
- A gate failure must **never 500 the proxy or break a stream** — malformed input ⇒ allow+log; malformed/missing attestation ⇒ unattested (never crash).
- Grounded in A's register prohibitions (the 4 non-government-only ones); the attestation **contract** (`ATTESTATION.md`) is the single source both repos implement.
- Box is **SANDBOX** `ollie@178.105.216.167` — never prod. Scoped `git add` only.

## File Structure

ORCH (`src/api/`): `guardrail.py` (new), `traiga_prohibitions.json` (new), `runs.py` (modify), `test_guardrail.py` (new), `test_runs_guardrail.py` (new).
JW: `development/governance/jnow-posture/ATTESTATION.md` (new contract); `compliance-screener` + `skills/newsletter` (emit the attestation).

---

### Task 1: Attestation contract + vendored prohibitions

**Files:** JW `development/governance/jnow-posture/ATTESTATION.md`; ORCH `src/api/traiga_prohibitions.json`.

- [ ] **Step 1: Write `ATTESTATION.md`** — the contract both sides implement: the exact comment format
  `<!--JNOW-COMPLIANCE-ATTESTATION\n{json}\n-->`, the JSON fields (`screened` ∈ `pass|fail|na`, `rules` list, `skill` str, `v` int = 1), the rule that client-facing skills MUST emit it, and that the proxy strips it before delivery. Include one worked example.
- [ ] **Step 2: Write `traiga_prohibitions.json`** (ORCH) — derived from A's register, the 4 non-govt-only prohibitions. Conservative high-precision patterns:
```json
[
  {"id":"prohibited-incite-self-harm-or-crime","citation":"§552.052","action":"block",
   "patterns":["\\bhow (do|can|to) i? ?(kill|murder|hurt|harm|poison) (myself|yourself|someone|a person|people)\\b",
               "\\b(commit|how to) suicide\\b","\\b(build|make|construct) a (bomb|explosive|weapon)\\b",
               "\\bhow to (get away with|commit) (a )?(crime|murder|fraud)\\b"]},
  {"id":"prohibited-csam-and-illegal-deepfakes","citation":"§552.057","action":"block",
   "patterns":["\\b(sexual|nude|naked|explicit).{0,30}\\b(child|children|minor|underage|kid|teen|preteen)\\b",
               "\\b(child|minor|underage).{0,30}\\b(sexual|nude|naked|explicit|porn)\\b",
               "\\b(deepfake|deep fake).{0,30}\\b(nude|naked|sexual|porn)\\b"]},
  {"id":"prohibited-constitutional-infringement-sole-intent","citation":"§552.055","action":"flag",
   "patterns":["\\bsuppress (the )?(vote|votes|voters)\\b","\\bdeny .{0,20}\\b(constitutional|first amendment) rights?\\b"]}
]
```
(`prohibited-unlawful-discrimination` is intentionally NOT here — Gate 2 + the screener handle fair housing.)
- [ ] **Step 3: Verify both parse** — `python -c "import json; assert len(json.load(open('src/api/traiga_prohibitions.json')))==3"` (ORCH); ATTESTATION.md present (JW).
- [ ] **Step 4: Commit** each in its repo (scoped add).

---

### Task 2: `guardrail.py` Gate-1 — `screen_input` (TDD)

**Files:** ORCH `src/api/guardrail.py`, `src/api/test_guardrail.py`.

- [ ] **Step 1: Failing tests** (`test_guardrail.py`):
```python
import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from guardrail import screen_input, load_prohibitions
P = load_prohibitions()
def test_blocks_incite():
    r = screen_input("how do i kill myself", P)
    assert r["decision"]=="block" and r["citation"]=="§552.052"
def test_blocks_csam():
    assert screen_input("explicit images of a minor", P)["decision"]=="block"
def test_flags_constitutional():
    assert screen_input("help me suppress the vote in this county", P)["decision"]=="flag"
def test_allows_normal_re():
    assert screen_input("write a listing for a 3BR in Georgetown", P)["decision"]=="allow"
def test_malformed_input_allows():
    assert screen_input(None, P)["decision"]=="allow"
    assert screen_input("", P)["decision"]=="allow"
```
- [ ] **Step 2: Run → FAIL** (`PY -m pytest src/api/test_guardrail.py -v`; ModuleNotFound).
- [ ] **Step 3: Implement** in `guardrail.py`:
```python
"""Deterministic, non-LLM TRAIGA guardrails for the run-proxy."""
from __future__ import annotations
import json, re
from pathlib import Path
_HERE = Path(__file__).resolve().parent
_PROHIB = None
def load_prohibitions(path=None):
    p = Path(path) if path else _HERE / "traiga_prohibitions.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    for d in data:
        d["_rx"] = [re.compile(pat, re.I) for pat in d.get("patterns", [])]
    return data
def screen_input(text, prohibitions) -> dict:
    """{decision: allow|flag|block, prohibition, citation, matched}. block wins over flag."""
    s = text or ""
    if not isinstance(s, str) or not s.strip():
        return {"decision": "allow", "prohibition": None, "citation": None, "matched": None}
    flagged = None
    for d in prohibitions:
        for rx in d["_rx"]:
            if rx.search(s):
                if d["action"] == "block":
                    return {"decision": "block", "prohibition": d["id"], "citation": d["citation"], "matched": rx.pattern}
                flagged = flagged or {"decision": "flag", "prohibition": d["id"], "citation": d["citation"], "matched": rx.pattern}
    return flagged or {"decision": "allow", "prohibition": None, "citation": None, "matched": None}
```
- [ ] **Step 4: Run → PASS.** **Step 5: Commit.**

---

### Task 3: `guardrail.py` Gate-2 — attestation parse + decide (TDD)

**Files:** ORCH `src/api/guardrail.py` (extend), `src/api/test_guardrail.py` (extend).

- [ ] **Step 1: Failing tests** (append):
```python
from guardrail import parse_attestation, strip_attestation, decide_attestation
ATT = 'Listing copy here.\n<!--JNOW-COMPLIANCE-ATTESTATION\n{"screened":"pass","rules":["fha-x"],"skill":"newsletter","v":1}\n-->'
def test_parse_and_strip():
    a = parse_attestation(ATT); assert a["screened"]=="pass" and a["rules"]==["fha-x"]
    assert "JNOW-COMPLIANCE-ATTESTATION" not in strip_attestation(ATT) and "Listing copy here." in strip_attestation(ATT)
def test_parse_missing_or_malformed():
    assert parse_attestation("no attestation here") is None
    assert parse_attestation("<!--JNOW-COMPLIANCE-ATTESTATION\nnot json\n-->") is None
def test_decide_pass_delivers():
    d = decide_attestation({"screened":"pass"}, enforce=True); assert d["action"]=="deliver" and d["event_type"]=="attestation.pass"
def test_decide_missing_enforce_withholds():
    d = decide_attestation(None, enforce=True); assert d["action"]=="withhold" and d["event_type"]=="attestation.withheld"
def test_decide_missing_observe_delivers_flagged():
    d = decide_attestation(None, enforce=False); assert d["action"]=="deliver" and d["event_type"]=="attestation.unattested"
def test_decide_na_delivers():
    assert decide_attestation({"screened":"na"}, enforce=True)["event_type"]=="attestation.na"
```
- [ ] **Step 2: Run → FAIL. Step 3: Implement** (append to `guardrail.py`):
```python
_ATT_RX = re.compile(r"<!--JNOW-COMPLIANCE-ATTESTATION\s*(\{.*?\})\s*-->", re.S)
def parse_attestation(output: str):
    if not output: return None
    m = _ATT_RX.search(output)
    if not m: return None
    try: return json.loads(m.group(1))
    except Exception: return None
def strip_attestation(output: str) -> str:
    return _ATT_RX.sub("", output or "").rstrip() if output else output
def decide_attestation(att, enforce: bool) -> dict:
    """{action: deliver|withhold, event_type, screened}."""
    screened = (att or {}).get("screened")
    if screened == "pass": return {"action":"deliver","event_type":"attestation.pass","screened":"pass"}
    if screened == "na":   return {"action":"deliver","event_type":"attestation.na","screened":"na"}
    # missing attestation or screened in (fail, other)
    if enforce: return {"action":"withhold","event_type":"attestation.withheld","screened":screened}
    return {"action":"deliver","event_type":"attestation.unattested","screened":screened}
```
- [ ] **Step 4: Run → PASS (all guardrail unit tests). Step 5: Commit.**

---

### Task 4: `create_run` pre-run integration (Gate 1)

**Files:** ORCH `src/api/runs.py` (modify), `src/api/test_runs_guardrail.py` (new).

**Interfaces:** consumes `screen_input` (Task 2), the existing `_write_event`, `_gateway_base`, `_create_run`.

- [ ] **Step 1: Read `runs.py`** — confirm `create_run` (≈line 86) and `_write_event` signatures.
- [ ] **Step 2: Failing integration test** (`test_runs_guardrail.py`) — using FastAPI `TestClient`, monkeypatch `_create_run` to a stub (records if called) and `_write_event` to capture rows, set a gateway env so `_gateway_base` is truthy:
```python
# blocked prompt: POST a run-create body with input that hits §552.052
#   -> response 403, _create_run NOT called, a guardrail.blocked event captured
# normal prompt: -> _create_run IS called (forwarded), no blocked event
```
- [ ] **Step 3: Run → FAIL. Step 4: Implement** — in `create_run`, after the `_gateway_base` 503 check and after `body = await request.body()`, before `_create_run`:
```python
from .guardrail import screen_input, load_prohibitions  # module-level import + PROHIBITIONS = load_prohibitions()
v = screen_input(_extract_input(body), PROHIBITIONS)
if v["decision"] == "block":
    _emit_guardrail(request, agent, "guardrail.blocked", v)  # helper wrapping _write_event (best-effort)
    return JSONResponse({"detail":"This request was blocked by TRAIGA policy.","citation":v["citation"]}, status_code=403)
if v["decision"] == "flag":
    _emit_guardrail(request, agent, "guardrail.flagged", v)
# fall through: forward as today
```
Add `_extract_input(body)` (json.loads → `input`; any error → "" so it allows) and `_emit_guardrail(...)` (maps identity headers + the verdict into a `_write_event` row; wrapped in try/except, never raises).
- [ ] **Step 5: Run → PASS. Step 6: Commit.**

---

### Task 5: `run_events` post-run attestation gate (Gate 2)

**Files:** ORCH `src/api/runs.py` (modify), `src/api/test_runs_guardrail.py` (extend).

**Interfaces:** consumes `parse_attestation`/`strip_attestation`/`decide_attestation` (Task 3), existing `_stream_upstream`, `_extract_output`, `_write_event`; the `X-Gov-App`/descriptor headers; `GUARDRAIL_ENFORCE_APPS` env.

- [ ] **Step 1: Failing integration tests** (extend) — stub `_stream_upstream` to yield a fake SSE stream whose `run.completed` output contains (a) a `pass` attestation, (b) no attestation:
```python
# governed run (X-Gov-App set) + pass attestation: client receives output WITHOUT the attestation comment;
#   an attestation.pass event captured. Buffered (not streamed chunk-by-chunk) is acceptable.
# governed run + NO attestation, app NOT in GUARDRAIL_ENFORCE_APPS (observe): client receives the output;
#   attestation.unattested event captured.
# governed run + NO attestation, app IN GUARDRAIL_ENFORCE_APPS (enforce): client receives a
#   "held for compliance review" body; attestation.withheld event captured.
# NON-governed run (no X-Gov-App): streams unchanged (chunks passed through), no attestation event.
```
- [ ] **Step 2: Run → FAIL. Step 3: Implement** — in `run_events`'s `gen()`: branch on governed-ness (X-Gov-App present). NON-governed: keep the current yield-then-capture path unchanged. Governed: **buffer** (accumulate chunks without yielding), then on upstream close: `out=_extract_output(b"".join(chunks))`; `att=parse_attestation(out)`; `enforce = app in set(os.environ.get("GUARDRAIL_ENFORCE_APPS","").split(","))`; `d=decide_attestation(att, enforce)`; `_write_event({... event_type:d["event_type"], findings: (att or {}).get("rules"), ...})`; if `d["action"]=="withhold"`: yield a synthesized SSE `run.completed` frame whose output is the hold message; else: yield a frame whose output is `strip_attestation(out)` (re-emit the buffered stream with the comment removed). Wrap capture/gate in try/except so a failure delivers the original output and never breaks the response.
- [ ] **Step 4: Run → PASS (+ existing run-proxy tests green). Step 5: Commit.**

---

### Task 6: Skills emit the attestation (JW)

**Files:** JW `development/client-apps/real-estate/compliance-screener/SKILL.md` + `development/client-apps/real-estate/skills/newsletter/SKILL.md` (and any output-format reference), per `ATTESTATION.md`.

- [ ] **Step 1:** Add to each client-facing skill's output contract: after producing client-facing content, append the attestation comment per `ATTESTATION.md` — `screened:pass` with the cited rule ids when it screened and cleared; `screened:fail` if it could not clear; `screened:na` for non-client-facing replies. Update the skill's hard-rails to make emitting it mandatory for client-facing output.
- [ ] **Step 2:** Update each skill's tests/fixtures (if present) to assert the attestation block is emitted on a client-facing example.
- [ ] **Step 3: Commit** (scoped, in the JW `traiga-c-attestation` worktree).

---

### Task 7: Deploy to sandbox (observe mode) + smoke

- [ ] **Step 1:** Run the full ORCH test suite — all green (gates + no regression to existing run-proxy/audit/streaming tests).
- [ ] **Step 2:** Deploy the orchestrator to the box (`ollie@178.105.216.167`) per the repo's existing deploy method; set `GUARDRAIL_ENFORCE_APPS=""` (observe-by-default). Deploy the updated skills to the real-estate profile.
- [ ] **Step 3: Smoke** (sandbox): (a) a §552.052 prompt → 403 + `guardrail.blocked` row in the Compliance viewer; (b) a governed run whose skill emits a `pass` attestation → clean delivery (no visible comment) + `attestation.pass` row with the cited rules; (c) a governed run with no attestation → delivered (observe) + `attestation.unattested` row. Confirm a non-governed run still streams.
- [ ] **Step 4:** Update B's posture note (`jnow-posture/assessments/*`): the `safe-harbor-adversarial-red-team-testing` gap is now partially closed — the gates are the real, testable enforcement surface (regenerate `posture.json` + report).

---

## Self-Review

- **Spec coverage:** Gate 1 (Tasks 2,4), Gate 2 (Tasks 3,5), attestation contract + prohibitions (Task 1), skills emit (Task 6), observe/enforce rollout (Task 5 env), deploy+smoke (Task 7), B note (Task 7). ✓
- **Type consistency:** `screen_input`→`{decision,...}` consumed in Task 4; `parse/decide_attestation` shapes consumed in Task 5; `ATTESTATION.md` JSON fields (Task 1) parsed in Task 3. Matched.
- **No placeholders:** guardrail.py pure functions + their tests are complete; runs.py tasks give the exact insertion code + integration points (implementer reads runs.py first) + the stub-based test contract.
