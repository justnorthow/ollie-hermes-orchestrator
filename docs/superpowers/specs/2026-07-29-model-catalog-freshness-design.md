# Model catalog freshness check

Date: 2026-07-29
Base: `c4f5359` (main)
Status: draft — awaiting review

## Problem

`src/catalog.py` defines the model catalog as a hardcoded Python literal:

```python
MODELS = [
    {"provider": "anthropic", "id": "claude-sonnet-4.6", "label": "Claude Sonnet 4.6"},
    {"provider": "anthropic", "id": "claude-opus-4.7",   "label": "Claude Opus 4.7"},
    {"provider": "openai",    "id": "gpt-5.5",            "label": "GPT-5.5"},
    {"provider": "groq",      "id": "llama-3.3-70b",      "label": "Llama 3.3 70B (Groq)"},
]
```

`list_models()` serves it to the dashboard's `ModelPicker`, and the selected id is
written into a profile's Hermes config as `model.default` by
`03-install-profile.sh:135` — verbatim, with no normalization on our side. Whatever
string is in this literal is what reaches the provider.

Nothing watches this list. As of today it carries two distinct defects.

**1. Two ids are malformed.** Anthropic model ids use hyphens, not dots:
`claude-sonnet-4-6`, `claude-opus-4-7`. The catalog has `claude-sonnet-4.6` and
`claude-opus-4.7`. A request with a dotted id returns a provider 404.

This is **latent, not live**: JNOW's boxes run `openai-codex` / `gpt-5.5`, and per JB
(2026-07-29) Anthropic models are not used with Hermes at all, because of the OAuth
limitation — so the Anthropic path has likely never been exercised. But both entries
are still offered in the dashboard picker, so any operator who selects one gets a
model that cannot authenticate. Two customer-visible dead options.

**2. The list is stale in both directions.** Retired models stay on offer
indefinitely; new ones never appear until someone hand-edits the literal. Current
reality diverges substantially from the four entries above — the Anthropic line is now
Fable 5 / Opus 5 / Sonnet 5 / Haiku 4.5, and per JB (2026-07-29) OpenAI has moved past
`gpt-5.5` to a GPT 5.6 family (Sol / Terra / Luna). The OpenAI ids and their
speed/cost characteristics are unconfirmed in this spec and are an input to slice 1.

Note that `gpt-5.5` is **not** malformed — OpenAI ids legitimately contain dots. Only
the Anthropic ids are wrong. Any id-format rule must therefore be per-provider; a
global "no dots" rule would break the one entry that currently works.

There is no test asserting that a catalog id is a real model, and no scheduled check
that would notice either defect. Both were found by hand, incidentally, while
designing an unrelated feature.

## Non-goals

- **Auto-adopting new models.** Detection is automatic; adoption is a human decision.
  See "Why adoption stays manual" below — this is the load-bearing constraint, not a
  scoping convenience.
- Changing how the catalog is consumed. `list_models()`, `ModelPicker`, and the
  `03-install-profile.sh` provider-inheritance path stay exactly as they are.
- Pricing display in the dashboard.
- Per-box model policy or entitlements (which models a given customer may select).
- Fixing the `list_skills()` literal in the same module. Same staleness pattern, but
  skills have a different source of truth and no provider API; out of scope here.

## What can and cannot be automated

The check is only useful if it is honest about this split.

| Fact | Source | Automatable |
|---|---|---|
| Model exists / has been retired | provider model list | ✅ |
| Context window, max output | provider model list | ✅ |
| Capability flags (thinking, vision, effort, structured outputs) | provider model list | ✅ |
| Id is well-formed | comparison against provider list | ✅ |
| **Price** | vendor pricing page only — not in any model API | ❌ |
| **Speed / cost class** (consult-eligibility) | operator judgment | ❌ |
| **Behavioral breaking changes** | release notes | ❌ |

That last row is what decides whether a model is *safe to offer*, and none of it is a
field. Recent examples, all of which would silently break a profile: thinking on by
default; `thinking: {type: "disabled"}` rejected above a given effort level;
`budget_tokens` removed; assistant prefill removed; sampling parameters rejected; a
30-day data-retention requirement that 400s every request from a ZDR org.

**Therefore the check produces a diff plus a review checklist, never a patch.**

## Design

### 1. Validation test — the highest-value assertion

**Every id in `MODELS` must appear in its provider's live model list.** An id that
does not is either a typo or a retirement, and both are things we want to know before
a customer selects it. This one assertion catches defect 1 above.

It is a pure set comparison, so it belongs in two places:

- `tests/test_catalog_ids.py` — runs in the existing pytest suite, against a checked-in
  fixture of known-good ids. Fast, offline, no credentials. Catches a malformed id at
  the moment someone edits the literal.
- The weekly job below — runs against the live provider list. Catches a retirement that
  happened without us editing anything.

The fixture and the live list are deliberately separate: the offline test protects
against typos, the online job protects against the world changing. Neither substitutes
for the other.

### 2. Weekly freshness job

Runs in **GitHub Actions** on a weekly cron in this repo, plus `workflow_dispatch` for
on-demand runs. Rationale: the catalog is one file in git shipped to every box, so this
is a repo-level concern, not a per-box one. Actions is repo-native, has a home for
secrets, can write the report and open the issue in the same run, and does not depend
on any particular machine being powered on.

Weekly, not monthly. Eight recent months carried six Anthropic launches and two
retirements; a monthly cadence would have been behind most of that.

> This repo has no `.github/workflows/` directory yet. The first slice creates it, and
> Actions may need enabling on `justnorthow/ollie-hermes-orchestrator`.

The job:

1. Loads `MODELS` from `src/catalog.py` (import, not parse).
2. For each distinct provider, fetches the current model list (see §5).
3. Diffs, producing four categories:
   - **Unknown** — catalog id absent from the provider list. Highest severity.
   - **New** — provider model absent from the catalog.
   - **Changed** — context window, max output, or capability flags differ from the
     recorded values.
   - **Unverifiable** — provider could not be reached or had no credentials.
4. Writes the report (§3). Exits non-zero only on **Unknown**, so a red run means a
   customer-visible dead option, not merely "a new model shipped."

### 3. Report sinks — file unconditional, Linear optional

**The file sink always runs.** `docs/model-catalog/latest.md` overwritten each run,
plus a dated copy under `docs/model-catalog/history/`, committed by the job. This is
the record, it is version-controlled, and it works with zero external configuration.

**The Linear sink is an optional adapter**, active only when credentials and a target
team are configured. It opens or updates a single issue when there is drift, so the
finding lands in the Open Engine queue as work rather than as a report someone has to
remember to read.

Per JB (2026-07-29): both, and the file sink must not depend on Linear — *"in case
Linear isn't in-use."* The check must never fail, skip, or go silent because Linear is
absent on a given instance. This mirrors the `off`-by-default posture used elsewhere in
the platform: the always-available path is the default, the richer path is opt-in.

An unconfigured Linear sink is logged as a skipped sink in the report, not an error.

### 4. Why adoption stays manual

A new model id appearing automatically in a customer box's picker is how an untested
API surface reaches a customer. Concretely, adopting Claude Fable 5 sight-unseen would
put an option in the picker that returns 400 on *every* request from a
zero-data-retention org, ignores any thinking configuration the profile sets, and can
run multi-minute single turns.

So the job reports and the human adopts, against this checklist:

- [ ] Id is well-formed and present in the provider's live list
- [ ] Price recorded (input / output per MTok)
- [ ] Auth path confirmed — API key vs OAuth (see §5)
- [ ] Thinking default, and whether disabling it is accepted
- [ ] Assistant prefill supported
- [ ] Sampling parameters accepted
- [ ] Data-retention or residency requirement
- [ ] Context window and max output recorded
- [ ] Speed / cost class assigned
- [ ] Which providers serve it

The checklist is emitted into the report for each **New** model, pre-populated with
whatever the provider list could answer, so the human fills in only the rows the API
cannot.

### 5. Provider coverage and auth

Cover all providers present in `MODELS`. Priority reflects actual use: **OpenAI first**
(the boxes run `openai-codex` / `gpt-5.5`), Groq second, Anthropic third — present in
the catalog but, per JB, not used with Hermes because of the OAuth limitation.

Anthropic stays in scope despite being unused: its two entries are the ones currently
malformed, and a customer box may hold an API key even where JNOW does not.

**Two fetch mechanisms, per provider, in order:**

1. **Provider model list API** — authoritative, structured, cheap. Requires an API key
   in Actions secrets.
2. **Docs / release-notes scrape** — fallback where no API key exists. Less precise,
   and cannot see capability flags, but it does answer "does this id still exist" and
   "what shipped recently," which is most of the value.

Mechanism 2 exists because a subscription OAuth token generally cannot enumerate models
programmatically — the same limitation that keeps Anthropic models out of Hermes may
apply to the model-list endpoints. **Open question for review: which providers do we
hold API keys for?** The answer decides which providers get mechanism 1; it does not
block the design, because every provider has a working fallback.

**Every run records which mechanism served each provider, and names any provider it
could not check at all.** A provider silently skipped would make a green run mean
"nothing changed" when it actually meant "we didn't look."

### 6. Catalog schema extension

Each `MODELS` entry gains three operator-set fields alongside `provider` / `id` /
`label`:

| Field | Meaning | Source |
|---|---|---|
| `speed_class` | `fast` \| `heavy` — consult-eligibility | operator judgment |
| `price_in` / `price_out` | $ per MTok | vendor pricing page |
| `verified_at` | date the entry was last human-reviewed | the adoption checklist |

`speed_class` is introduced here rather than in the dispatch spec so that dispatch
consumes an existing field instead of adding one. `verified_at` is what makes staleness
visible even when no diff is detected — an entry nobody has looked at in a year is a
finding in its own right, and the weekly report lists entries older than a
configurable threshold.

These fields are additive and optional at read time: `list_models()` keeps its current
response shape for any consumer that ignores them, so `ModelPicker` needs no change in
this spec.

## Risks

- **Actions not enabled on the repo.** Blocks slice 2. Detected immediately on first
  push; falls back to `workflow_dispatch`-only or a scheduled Claude Code routine.
- **Scrape fragility.** Vendor doc pages change layout. Mitigation: the scrape path
  reports "unverifiable" rather than asserting a wrong answer, and unverifiable never
  fails the build.
- **Noisy diffs after a vendor launch wave.** A week with three launches produces a
  large **New** list. Acceptable: **New** does not fail the run, and the report groups
  by provider.
- **Fixture drift.** The offline test's known-good fixture is itself hand-maintained,
  so it can go stale the same way the catalog did. Mitigation: the weekly job validates
  the fixture against the live list too, and flags divergence.

## Testing

- `tests/test_catalog_ids.py` — every `MODELS` id present in the fixture; id format
  matches the provider's convention; no duplicate ids; every entry has a provider,
  id, and label.
- Diff-engine unit tests with fakes per provider — the four categories, plus the
  empty-diff case, plus a provider that raises.
- Sink tests — file sink writes both paths; Linear sink is skipped and *reported as
  skipped* when unconfigured; file sink still writes when the Linear sink raises.
- Exit-code test — non-zero on **Unknown** only.

The diff engine and sinks take injected fetchers and writers, so the whole suite runs
offline with no credentials.

## Slices

1. **Fix the two malformed ids**, refresh the catalog to current models, and add the
   §6 schema fields. Standalone, ships immediately, no dependency on the rest.
   Requires the confirmed OpenAI 5.6 ids as an input.
2. **Offline validation test** + the known-good fixture, with per-provider id-format
   rules.
3. **Diff engine + file sink**, runnable locally via `python -m`.
4. **Actions workflow** on a weekly cron with `workflow_dispatch`.
5. **Linear sink** as an optional adapter.

Slices 2–3 have no external dependencies. Slice 1 needs the confirmed OpenAI ids.
Slice 4 needs Actions enabled and whatever API keys §5 resolves to. Slice 5 needs
Linear credentials and a target team.

## Relationship to the dispatch spec

The switchable-dispatch design (separate spec, to follow) includes a cheap-peer rule
that gates synchronous agent-to-agent consults on a model's speed class. That rule
reads the same catalog. This spec is sequenced first so the classification it depends
on is accurate and stays that way.

The speed / cost class field is added to the catalog **here** — as an operator-set
value with no automated source — so the dispatch work can consume it rather than
introduce it.
