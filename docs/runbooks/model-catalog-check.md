# Model catalog freshness check

Weekly automated check that `src/catalog.py`'s `MODELS` still matches what
each provider actually offers. Design: `docs/superpowers/specs/2026-07-29-model-catalog-freshness-design.md`.

## What it does

Scrapes each provider's public docs page (unauthenticated — an unattended
weekly job cannot hold short-lived OAuth tokens), diffs the extracted model
ids against `MODELS`, and writes a markdown report to
`docs/model-catalog/latest.md` (plus a dated copy under
`docs/model-catalog/history/`). Runs via GitHub Actions on Mondays 06:17 UTC
(`.github/workflows/model-catalog-check.yml`), and on demand via
`workflow_dispatch`.

The **file sink always runs**, with zero configuration. It is the record.

## Running it by hand

```bash
python -m src.catalog_check [--root docs/model-catalog]
```

`--root` defaults to `docs/model-catalog`. This makes real, unauthenticated
network requests to provider docs pages — do not run it in a sandbox with no
network, and do not run it in tests (the test suite injects a fake `fetch`
so it stays fully offline).

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Clean, or drift that is not blocking (new model available, provider unverifiable, entry overdue for review, id not on a partial source). |
| `1` | Blocking: an id in the catalog that the provider no longer lists, on a source that lists everything it serves — a typo or a real retirement. Customer-visible dead option. |
| `2` | The check itself failed unexpectedly (a bug, an I/O error) — not a finding. Read the job log, not `latest.md`. |

The GitHub Actions job's final step reads the real exit code and reports
each case differently, so a crash is never mistaken for "catalog has unknown
ids" or vice versa.

## What a missing id does and does not prove

Presence and absence are not symmetric evidence. Finding an id on a provider's
docs page proves the model exists. **Not** finding one proves only that the
page does not document it — which is a different claim, and a weaker one.

Each provider therefore declares `absence_is_authoritative` in
`src/catalog_check/providers.py`. Where it is true, a missing catalog id is
reported as **Unknown ids — BLOCKING** and fails the run. Where it is false,
it is reported as **Not on the checked page — REVIEW** and does not.

`openai` is false, and this is the reason: the boxes do not reach OpenAI
through the first-party API that `platform.openai.com/docs/models` describes.
Hermes's `openai-codex` provider talks to `chatgpt.com/backend-api/codex`,
which on 2026-07-30 served ten ids where that page accounted for three —
`gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.3-codex-spark` and `-pro`
variants of the whole 5.6 family are all available and none are documented
there.

That is not a hypothetical. On 2026-07-30 this check reported `gpt-5.5`
missing, the run went red, and the model was deleted from `src/catalog.py` —
while it was still being served and while `tests/conftest.py` still seeded it
as a profile's `model.default`. The check had talked us out of offering the
model the boxes boot with.

**So: never delete an id from `src/catalog.py` on a REVIEW finding alone.**
Confirm against the surface the boxes actually use — open a profile's
dashboard and read its model picker. Checking the Codex backend automatically
would need an OAuth token, which an unattended weekly job cannot hold.

## Linear sink (optional)

Set both `LINEAR_API_KEY` and `LINEAR_TEAM_ID` to also open a Linear issue
when the run has a blocking finding (see `.env.example`). Unset either one
and the sink is skipped — logged, not an error. The Linear sink never
affects the file sink; it cannot fail the run.

It only opens an issue on a **blocking** finding, not on any drift. A
catalog entry that is merely stale (`verified_at` overdue or `"never"`) does
not open one — several entries carry `verified_at: "never"` by design, so
gating on "any drift" would reopen the same issue every single run,
forever. There is no update/dedupe path yet: if Linear is configured and
the same blocking finding persists across runs, expect a new issue each
time until it is resolved.

## Adoption is manual by design

The check never writes to `MODELS`. A "new models available" section in the
report proposes candidates with an adoption checklist (price, auth path,
thinking defaults, sampling params, data-retention requirements, context
window, speed class, which providers serve it, ...) — a human works the
checklist, then hand-edits `src/catalog.py` and
`tests/fixtures/known_models.json`. Price and behavioral changes are not
knowable from a provider's model-list API at all, which is the whole reason
this can't be automated further. To bound report length, only the first 5
new ids per run get a full checklist; the rest are listed as one-line
bullets under "Further candidates needing triage."

## Known outstanding finding: `openai/gpt-5.5`

`openai/gpt-5.5` is currently in the catalog but absent from OpenAI's live
docs list (which lists `gpt-4`, `gpt-5`, `gpt-5.6`, `gpt-5.6-sol`,
`gpt-5.6-terra`, `gpt-5.6-luna`) — **the first runs are expected to exit 1
on it.** This is not a scraper bug.

Retiring it is blocked on two open decisions:

- Whether GPT-5.6 Terra should be `speed_class: fast` or Luna-only.
- Whether Sol is entitled on the `openai-codex` channel.

`tests/conftest.py`'s `fake_env` fixture seeds `gpt-5.5` as the default
profile model (`model.default: gpt-5.5` in the fake `.hermes/config.yaml`),
so retiring it from `src/catalog.py` also means updating that fixture —
budget for it as part of whichever change finally retires the id.
