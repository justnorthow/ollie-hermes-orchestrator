# TRAIGA Sub-project C (v1) — Run-Proxy Guardrails: Prohibited-Use + Compliance Attestation — Design Spec

**Date:** 2026-06-29
**Status:** Draft (brainstorming settled, attestation pulled into v1) — pending user review
**Program:** TRAIGA compliance, Sub-project **C** of 4 (A → B → **C** → D). This is **C v1**.
**Repos:** `ollie-hermes-orchestrator` (the run-proxy) **and** `jnow-workspace` (the skills emit attestations).
Grounds in `jnow-workspace` Sub-project A's register.

## Context

A built the requirements **register**; B documented JNOW's governance **posture** and left one honest open
gap per system: **enforcement / live red-team is not yet real**. **C makes the guardrails real and
server-side at the run-proxy** — an in-path, non-LLM, *unskippable* gate every governed run already
traverses (nginx injects the gateway keys; the Hermes gateway is reachable only through the orchestrator).

Brainstorming settled — **focus:** guardrails first, trust features later; **enforcement point:** centralized
at the run-proxy; **v1 scope:** **pre-run prohibited-use refusal + post-run compliance-attestation gate +
audit.** (The consumer disclosure/trust layer is **C phase-2**.)

> **The attestation gate was pulled into v1 deliberately (product decision):** real clients need it from day
> one — *provable, server-side proof that every client-facing AI output was compliance-screened, that a skill
> cannot silently skip* — and it is a core part of the moat. It is the higher-value of the two gates.

The run-proxy today (`src/api/runs.py`, governance-audit-substrate SP1) is a transparent pass-through with
post-hoc audit only. C v1 adds **two deterministic gates**, both reusing the existing `governance_events`
audit path:

1. **Pre-run prohibited-use gate** (in `create_run`) — refuse egregious prohibited requests before forwarding.
2. **Post-run attestation gate** (in `run_events`) — for governed client-facing runs, verify the output
   carries a valid compliance-screening **attestation**; release if attested, withhold/flag if not.

## Gate 1 — Pre-run prohibited-use (grounded in A's register)

A's register has 6 prohibitions; two are **government-only** (biometric §552.054, social scoring §552.053) →
N/A for JNOW. Of the rest:

| Prohibition | Citation | Gate-able at the prompt? | v1 handling |
|---|---|---|---|
| Incite self-harm / harm / crime | §552.052 | Yes (pattern) | **Block (403)** |
| CSAM / illegal deepfakes / minor-sexual | §552.057 | Yes (pattern) | **Block (403)** |
| Sole-intent constitutional infringement | §552.055 | Weak (intent) | **Flag** (forward + log) |
| Unlawful discrimination (intent) | §552.056 | No (output-level) | Handled by **Gate 2** + the screener skill |

A deterministic, high-precision **egregious-use backstop** (cheap insurance + §552.105(e)(2)(B) evidence).
A RE agent realistically never sees these; the value is the unskippable server-side block + the audit record.

## Gate 2 — Post-run compliance attestation (the moat)

**Claim it proves to a client:** *every client-facing AI output from your brokerage was compliance-screened,
and the platform enforces it server-side — a skill cannot ship unscreened content.*

**Mechanism (keeps the proxy deterministic/non-LLM — the screening intelligence stays in the skill):**
1. **Skills emit an attestation.** When a client-facing skill (compliance-screener, newsletter, and future
   client-facing skills) produces output, it appends a machine-readable, consumer-invisible attestation:
   ```
   <final client-facing content>

   <!--JNOW-COMPLIANCE-ATTESTATION
   {"screened":"pass","rules":["fha-...","trec-535-155"],"skill":"newsletter","v":1}
   -->
   ```
   `screened` ∈ `pass | fail | na`. (`na` = the skill is non-client-facing / nothing to screen.)
2. **The proxy verifies + strips it.** For a **governed** run (one carrying the existing `X-Gov-App` /
   `X-Gov-Event-Type` descriptor headers), `run_events` **buffers** the output (instead of streaming),
   parses the attestation, then:
   - `screened == pass` → **strip** the attestation comment, deliver the clean content, emit
     `governance_events(event_type="attestation.pass", findings=rules)`.
   - attestation **missing** or `screened == fail` → **enforce mode:** withhold the output, deliver a
     "held for compliance review" message, emit `attestation.withheld`. **observe mode:** deliver the content
     but emit `attestation.unattested` (so rollout is safe — start observe, flip to enforce per app).
   - `screened == na` → deliver, emit `attestation.na`.
3. The consumer never sees the attestation comment; the **audit trail** holds the provable record.

**Trust boundary (stated honestly):** the proxy enforces the attestation is *present and pass* — it catches a
skill that **omits** screening (the realistic failure: a new/edited skill that forgot the gate). It does not
catch a skill that *lies* (emits `pass` without screening); that is an internal trust boundary JNOW controls
and tests. For clients, the attestation + audit trail is the provable, enforced record.

**Streaming trade-off:** governed runs become **non-streaming** (buffered) so the output can be gated before
release. Acceptable for compliance-gated client-facing content; **non-governed runs stream unchanged.**

**Rollout safety:** `GUARDRAIL_ENFORCE_APPS` (env, comma-sep app ids) controls which governed apps are in
**enforce** vs **observe** mode. v1 ships observe-by-default; flip apps to enforce once their skills emit
attestations and the events look clean.

## Architecture

**`ollie-hermes-orchestrator`:**
```
src/api/
  guardrail.py            # NEW — pure: screen_input() (Gate 1) + parse_attestation()/decide() (Gate 2)
  traiga_prohibitions.json# NEW — vendored prohibition patterns derived from A's register (Gate 1)
  runs.py                 # MODIFY — create_run: pre-run screen (Gate 1);
                          #          run_events: buffer governed runs, attestation gate (Gate 2)
  test_guardrail.py       # NEW — unit: screen_input + attestation parse/decide
  test_runs_guardrail.py  # NEW — integration: 403 block; withhold/observe; pass strips + delivers; non-governed streams
```

**`jnow-workspace` (skills emit the attestation):**
```
development/client-apps/real-estate/compliance-screener/   # screener output appends the attestation block
development/client-apps/real-estate/skills/newsletter/     # newsletter output appends the attestation block
development/governance/jnow-posture/ATTESTATION.md          # NEW — the shared attestation contract (format + screened values)
```
The attestation **contract** (`ATTESTATION.md`) is the single source of truth both sides implement; the
orchestrator vendors a copy of the parse rules (the box has no jnow-workspace tree).

## Data flow

```
browser → nginx (X-Auth-*) → orchestrator
  create_run:  screen_input(input)
                 block → 403 + governance_events(guardrail.blocked)         [NOT forwarded]
                 flag  → governance_events(guardrail.flagged) → forward
                 allow → forward
  run_events (governed run): buffer output → parse_attestation → decide
                 pass        → strip comment, deliver, governance_events(attestation.pass)
                 missing/fail→ enforce: withhold + attestation.withheld | observe: deliver + attestation.unattested
                 na          → deliver, attestation.na
  run_events (non-governed): stream unchanged
```

## Trust / evidence bar

- Both gates are **deterministic + server-side** — unbypassable by prompt injection (not the LLM), run on
  every governed create/stream. This is the *unskippable enforcement* B's posture lacked; it converts the
  per-system `safe-harbor-adversarial-red-team-testing` gap toward `applies` (red-team the gates as one
  surface) and is the enforced basis for the client moat claim.
- Every decision is **audited** (`governance_events`, append-only) — §552.105(e)(2)(B) evidence + a queryable
  per-output screening record clients can be shown.
- **Honest scope:** Gate 1 blocks only the two unambiguous egregious prohibitions; Gate 2 enforces *presence*
  of screening, not its correctness; §552.056 correctness stays with the screener. No overclaim.

## Success criteria (C v1 done)

1. `guardrail.py`: `screen_input` (block/flag/allow, malformed-input→allow) and `parse_attestation`/`decide`
   (pass/withhold/unattested/na, missing-or-malformed comment handled) — unit-tested.
2. `create_run` blocks a §552.052/§552.057 prompt (403 + `guardrail.blocked`, not forwarded); normal prompt
   unchanged. `run_events`: a governed run with a `pass` attestation delivers clean content (comment stripped)
   + `attestation.pass`; a governed run **missing** attestation withholds (enforce) or flags (observe); a
   **non-governed** run streams unchanged — all integration-tested with a stubbed gateway.
3. The compliance-screener + newsletter skills emit a valid attestation block on client-facing output (skill
   tests updated); the `ATTESTATION.md` contract exists.
4. Existing run-proxy + audit tests still pass (no regression to streaming on non-governed runs).
5. Deployed to the **sandbox** orchestrator (`178.105.216.167`, NOT prod) with `GUARDRAIL_ENFORCE_APPS`
   observe-by-default; smoke: a blocked prompt → 403 + event; a governed run with a `pass` attestation →
   clean delivery + `attestation.pass` row in the Compliance viewer; a forged un-attested governed run →
   `attestation.unattested` (observe) row.
6. B's posture note updated: the red-team gap is now partially closed (the gates are the testable surface).

## Explicitly out of scope (C v1)

- **Consumer disclosure / trust features** (voluntary "AI-assisted, governed" surface + plain-language
  explanation) — C phase-2.
- **§552.056 fair-housing correctness** at the proxy — stays with the screener skill (Gate 2 enforces that
  screening *ran*, not that its verdict is right).
- An **LLM classifier** in the proxy — deliberately avoided (gates stay deterministic, fast, unfailing).
- **Cryptographic signing** of attestations (a skill that lies is an internal trust boundary) — a possible
  phase-2 hardening, not v1.
- **Auto-syncing** `traiga_prohibitions.json` / the attestation parser from the live register — v1 vendors
  them; refresh is a documented manual step.
- **Sub-project D** (client TRAIGA-readiness offering — it consumes C's enforced attestations as proof).
