# TRAIGA Sub-project C (v1) — Pre-Run Prohibited-Use Guardrail — Design Spec

**Date:** 2026-06-29
**Status:** Draft (brainstorming settled) — pending user review
**Program:** TRAIGA compliance, Sub-project **C** of 4 (A → B → **C** → D). This is **C v1**.
**Repo:** `ollie-hermes-orchestrator` (the run-proxy lives here). Grounds in `jnow-workspace` A register.

## Context

TRAIGA Sub-project A built the requirements **register**; Sub-project B documented JNOW's governance
**posture** and left one honest open gap per system: **live-agent red-team / enforcement is not yet real**.
**C makes the defensive guardrails real and server-side.** Brainstorming settled:
- **Focus:** both guardrails + optional trust features, **guardrails first**.
- **Enforcement point:** **centralized at the orchestrator run-proxy** — an in-path, server-side, non-LLM,
  *unskippable* gate every governed run already traverses (nginx injects the gateway keys; the Hermes gateway
  is only reachable through the orchestrator).
- **v1 scope:** **pre-run prohibited-use refusal + audit.** (Post-run output gating and the consumer
  disclosure/trust layer are **C phase-2**, explicitly out of scope here.)

The run-proxy today (`src/api/runs.py`, governance-audit-substrate SP1) is a transparent pass-through with
post-hoc audit only. C v1 adds a **pre-run screen** in `create_run`: parse the input, run a deterministic
TRAIGA prohibited-use check, and **refuse (HTTP 403) before forwarding** on a violation — emitting a
`governance_events` row either way. This is the ~10-line-core enforcement the feasibility review identified,
with streaming for allowed runs **unchanged**.

## What the gate enforces (grounded in A's register)

A's register has 6 prohibitions. Two are **government-only** (biometric §552.054, social scoring §552.053) →
**N/A** for JNOW (private). Of the remaining four:

| Prohibition | Citation | Deterministically gate-able at the prompt? | C v1 handling |
|---|---|---|---|
| Incite self-harm / harm / crime | §552.052 | **Yes** — pattern-detectable | **Pre-run block** |
| CSAM / illegal deepfakes / minor-sexual | §552.057 | **Yes** — pattern-detectable | **Pre-run block** |
| Sole-intent constitutional infringement | §552.055 | Weak (intent-based) | Logged as `flagged`, not blocked (low false-positive bar) |
| Unlawful discrimination (intent) | §552.056 | No (intent; output-level) | **Out of scope of the gate** — handled downstream by the compliance-screener skill (fair-housing). Noted in the report. |

So the v1 gate is a **deterministic egregious-use backstop** for §552.052 + §552.057 (cheap insurance + real
§552.105(e)(2)(B) red-team/feedback evidence), and a soft `flagged` signal for §552.055. The realistic RE
compliance risk (§552.056 fair housing) stays with the screener — the gate does not duplicate it.

## Architecture (orchestrator changes only; no Hermes/agent changes)

```
src/api/
  guardrail.py          # NEW — deterministic prohibited-use screen (pure, testable, non-LLM)
  traiga_prohibitions.json  # NEW — vendored prohibition patterns derived from A's register
  runs.py               # MODIFY — create_run: screen input before forwarding; emit guardrail event
  test_guardrail.py     # NEW — unit tests for the screen
  test_runs_guardrail.py# NEW — integration: blocked run returns 403 + emits event + does NOT forward
```

### `traiga_prohibitions.json` (vendored, grounded)

A small file derived from A's register (kept in the orchestrator so the box has it; refreshed when the
register changes — a documented manual step, since the orchestrator does not have the jnow-workspace tree).
Shape — one entry per gate-able prohibition:
```json
[
  {"id": "prohibited-incite-self-harm-or-crime", "citation": "§552.052", "action": "block",
   "patterns": ["how (do|can) i (kill|hurt|harm) ...", "...suicide method...", "...make a bomb..."]},
  {"id": "prohibited-csam-and-illegal-deepfakes", "citation": "§552.057", "action": "block",
   "patterns": ["...sexual ... (child|minor|underage)...", "...deepfake ... nude...", ...]},
  {"id": "prohibited-constitutional-infringement-sole-intent", "citation": "§552.055", "action": "flag",
   "patterns": [...]}
]
```
Patterns are conservative (high-precision, low false-positive) regexes/keywords — the goal is to catch
unambiguous egregious requests, not to be a content moderator. A RE agent realistically never sees these;
the value is the unskippable backstop + safe-harbor evidence.

### `guardrail.py`

```
screen_input(text: str, prohibitions: list) -> dict
  # returns {"decision": "allow" | "block" | "flag",
  #          "prohibition": <id or None>, "citation": <str or None>, "matched": <pattern or None>}
  # block on the first action=block match; else flag on an action=flag match; else allow.
```
Pure, deterministic, no I/O — unit-tested in isolation. Loads `traiga_prohibitions.json` once at import.

### `runs.py` — `create_run` integration

Before forwarding (the body is already `await request.body()`):
```python
verdict = screen_input(extract_input(body), PROHIBITIONS)
if verdict["decision"] == "block":
    _write_event({... event_type: "guardrail.blocked", status: "blocked",
                  title: verdict["citation"], findings: verdict["prohibition"], content: <redacted/snippet>})
    return JSONResponse({"detail": "This request was blocked by TRAIGA policy.",
                         "citation": verdict["citation"]}, status_code=403)
# flag -> emit a "guardrail.flagged" event but FORWARD (do not block); allow -> forward as today
```
- `extract_input(body)` parses the run-create JSON `input` (defensive: malformed body → allow + log, never
  500 the proxy).
- Guardrail events reuse the existing `_write_event` path + `governance_events` schema (append-only,
  service-role insert). New `event_type`s: `guardrail.blocked`, `guardrail.flagged`. The blocked event stores
  a redacted snippet (not the full prohibited text) — do not persist verbatim egregious content.
- **Allowed runs are byte-for-byte unchanged** (no added latency beyond a regex scan; streaming intact).

## Data flow

```
browser → nginx (injects X-Auth-*) → orchestrator create_run
    → screen_input(input, PROHIBITIONS)
        block → 403 + governance_events(guardrail.blocked)         [run NOT forwarded]
        flag  → governance_events(guardrail.flagged) → forward      [run proceeds]
        allow → forward to Hermes gateway                           [unchanged]
```

## Trust / evidence bar

- The gate is **deterministic and server-side** — it cannot be bypassed by prompt injection (it is not the
  LLM) and runs on every governed create. This is exactly the *unskippable enforcement* B's posture said it
  lacked; it converts the per-system `safe-harbor-adversarial-red-team-testing` gap toward `applies`
  (red-team the gate as one surface).
- Every decision is **audited** (`governance_events`), giving §552.105(e)(2)(B) "discovers via testing/
  feedback" evidence and a queryable record.
- **Honest scope:** v1 blocks only the two unambiguous egregious prohibitions; §552.056 fair-housing stays
  with the screener; intent-based §552.055 is flag-only. The spec does not overclaim coverage.

## Success criteria (C v1 done)

1. `guardrail.py` + `traiga_prohibitions.json` exist; `screen_input` returns block/flag/allow correctly
   (unit-tested incl. the malformed-input → allow path).
2. `create_run` blocks a §552.052/§552.057 prompt with a 403 + a `guardrail.blocked` event and does **not**
   forward; a flagged prompt forwards but emits `guardrail.flagged`; a normal prompt is byte-for-byte
   unchanged (integration-tested with a stubbed gateway).
3. Existing run-proxy tests still pass (no regression to the audit path or streaming).
4. Deployed to the **sandbox** orchestrator (`178.105.216.167`, NOT prod); a smoke test shows a blocked
   prompt returns 403 and lands a `governance_events` row visible in the Compliance viewer.
5. A short note updates B's posture: the red-team gap is now partially closed (the gate is the testable
   enforcement surface).

## Explicitly out of scope (C v1)

- **Post-run output gating** (buffer-and-check governed outputs before release) — C phase-2.
- **Consumer disclosure / trust features** (voluntary "AI-assisted, governed" surface + explanation) — C phase-2.
- **§552.056 fair-housing intent** detection at the gate — stays with the compliance-screener skill.
- An LLM classifier in the proxy — deliberately avoided (keeps the gate deterministic, fast, unfailing).
- **Sub-project D** (client TRAIGA-readiness offering).
- Auto-syncing `traiga_prohibitions.json` from the live register — v1 vendors it; refresh is a documented manual step.
