# Model Catalog Freshness Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect when the hand-maintained model catalog in `src/catalog.py` drifts from reality — malformed ids, retired models, newly shipped models — and report it weekly without ever auto-adopting a model.

**Architecture:** Three layers, each independently testable. (1) An offline validation test asserting every catalog id is well-formed and known. (2) A diff engine that compares the catalog against provider model lists supplied by injected fetchers, producing four categories. (3) Sinks — a file sink that always runs and a Linear sink that activates only when configured. A GitHub Actions weekly cron drives layers 2 and 3 using unauthenticated docs scraping, because no API key can live in CI.

**Tech Stack:** Python 3.11+, pytest (`asyncio_mode = auto`, `pythonpath = .`), `httpx` for fetching, `dataclasses` for result types. GitHub Actions for scheduling. No new dependencies — `httpx` and `pyyaml` are already in `requirements.txt`.

**Spec:** `docs/superpowers/specs/2026-07-29-model-catalog-freshness-design.md`

## Global Constraints

- **No new runtime dependencies.** `httpx>=0.27.2` and `pyyaml>=6.0` are already present; use them. Do not add `requests`, `beautifulsoup4`, `respx`, or a Linear SDK.
- **`list_models()` response shape must not change for existing consumers.** `src/api/catalog.py:11` returns `{"models": list_models()}` and the dashboard's `ModelPicker` reads `id` / `provider` / `label`. New fields are additive and optional.
- **The file sink is unconditional. The Linear sink is optional.** The check must never fail, skip, or go silent because Linear is unconfigured on a given instance.
- **Exit non-zero on `unknown` only.** A newly shipped model must not turn the weekly run red.
- **Id format rules are per-provider.** `gpt-5.5` is a valid OpenAI id — dots are legitimate there. Only Anthropic ids are hyphen-only. A global "no dots" rule is a bug.
- **Adoption stays manual.** No task may add code that writes a newly discovered model into `MODELS`.
- **Silence is failure.** A provider that cannot be checked is reported as `unverifiable`, never omitted.
- **All tests run offline with no credentials.** Every fetcher and writer is injected.

---

## File Structure

| Path | Responsibility |
|---|---|
| `src/catalog.py` | **Modify.** The `MODELS` literal + `list_models()`. Gains optional metadata fields. |
| `src/catalog_rules.py` | **Create.** Per-provider id-format rules and catalog validation. No I/O. |
| `src/catalog_check/__init__.py` | **Create.** Package marker. |
| `src/catalog_check/types.py` | **Create.** `ProviderResult`, `Diff` dataclasses. Shared vocabulary, no logic. |
| `src/catalog_check/diff.py` | **Create.** Pure diff engine. Takes catalog + provider results, returns `Diff`. |
| `src/catalog_check/providers.py` | **Create.** Scrapers. One `scrape_provider()` plus per-provider config. Injected fetch. |
| `src/catalog_check/sinks.py` | **Create.** File sink (always) + Linear sink (optional) + `run_sinks()` orchestration. |
| `src/catalog_check/state.py` | **Create.** Reads/writes the consecutive-unverifiable counter. |
| `src/catalog_check/__main__.py` | **Create.** CLI entry point wiring fetchers → diff → sinks → exit code. |
| `tests/fixtures/known_models.json` | **Create.** Known-good id list per provider, hand-maintained. |
| `tests/test_catalog_rules.py` | **Create.** Id-format rules + catalog validation. |
| `tests/test_catalog_diff.py` | **Create.** Diff engine categories. |
| `tests/test_catalog_providers.py` | **Create.** Scraper behavior incl. min-count guard. |
| `tests/test_catalog_sinks.py` | **Create.** Sink independence and failure isolation. |
| `tests/test_catalog_state.py` | **Create.** Escalation counter. |
| `.github/workflows/model-catalog-check.yml` | **Create.** Weekly cron + `workflow_dispatch`. |

Rationale for splitting `catalog_check/` into five small modules rather than one file: `diff.py` and `state.py` are pure functions worth reading in isolation, `providers.py` is the only module that touches the network, and `sinks.py` is the only one that writes. That boundary is what makes the whole suite runnable offline.

---

### Task 1: Per-provider id-format rules

**Files:**
- Create: `src/catalog_rules.py`
- Test: `tests/test_catalog_rules.py`

**Interfaces:**
- Consumes: `src.catalog.MODELS` (existing list of dicts with `provider`, `id`, `label`).
- Produces:
  - `id_format_errors(provider: str, model_id: str) -> list[str]`
  - `validate_catalog(models: list[dict], known: dict[str, list[str]]) -> list[str]`

  Both return a list of human-readable error strings; empty list means valid.

- [ ] **Step 1: Write the failing test**

Create `tests/test_catalog_rules.py`:

```python
import pytest

from src.catalog_rules import id_format_errors, validate_catalog


def test_anthropic_hyphenated_id_is_valid():
    assert id_format_errors("anthropic", "claude-sonnet-5") == []
    assert id_format_errors("anthropic", "claude-opus-4-8") == []
    assert id_format_errors("anthropic", "claude-haiku-4-5") == []


def test_anthropic_dotted_id_is_invalid():
    errors = id_format_errors("anthropic", "claude-sonnet-4.6")
    assert errors
    assert "hyphen" in errors[0].lower()


def test_openai_dotted_id_is_valid():
    # gpt-5.5 is a real OpenAI id. A global no-dots rule would be a bug.
    assert id_format_errors("openai", "gpt-5.5") == []
    assert id_format_errors("openai", "gpt-5.6-luna") == []


def test_universal_rules_reject_junk():
    assert id_format_errors("groq", "Llama-3.3-70B")   # uppercase
    assert id_format_errors("groq", "llama 3.3 70b")   # whitespace
    assert id_format_errors("groq", "")                # empty


def test_unknown_provider_gets_universal_rules_only():
    assert id_format_errors("someprovider", "model-1.2") == []
    assert id_format_errors("someprovider", "Model X")


def test_validate_catalog_flags_id_absent_from_known_list():
    models = [{"provider": "openai", "id": "gpt-9.9", "label": "GPT-9.9"}]
    known = {"openai": ["gpt-5.5"]}
    errors = validate_catalog(models, known)
    assert any("gpt-9.9" in e for e in errors)


def test_validate_catalog_flags_duplicate_ids():
    models = [
        {"provider": "openai", "id": "gpt-5.5", "label": "A"},
        {"provider": "openai", "id": "gpt-5.5", "label": "B"},
    ]
    errors = validate_catalog(models, {"openai": ["gpt-5.5"]})
    assert any("duplicate" in e.lower() for e in errors)


def test_validate_catalog_flags_missing_required_field():
    models = [{"provider": "openai", "id": "gpt-5.5"}]  # no label
    errors = validate_catalog(models, {"openai": ["gpt-5.5"]})
    assert any("label" in e for e in errors)


def test_validate_catalog_passes_clean_input():
    models = [{"provider": "openai", "id": "gpt-5.5", "label": "GPT-5.5"}]
    assert validate_catalog(models, {"openai": ["gpt-5.5"]}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_catalog_rules.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.catalog_rules'`

- [ ] **Step 3: Write minimal implementation**

Create `src/catalog_rules.py`:

```python
"""Validation rules for the model catalog.

Pure functions, no I/O. The primary correctness check is membership in a
known-model list; the format rules are a cheap typo guard layered on top.

Format rules are deliberately PER-PROVIDER. OpenAI ids legitimately contain
dots (`gpt-5.5`), so a global no-dots rule would reject the one entry that
currently works. We encode only conventions we have verified.
"""
import re

# Applies to every provider: lowercase, starts alphanumeric, no whitespace.
_UNIVERSAL = re.compile(r"^[a-z0-9][a-z0-9.\-]*$")

# Verified conventions only. Anthropic ids are hyphen-separated; a dot is the
# specific defect this catches (`claude-sonnet-4.6` should be `claude-sonnet-4-6`).
_PROVIDER_RULES = {
    "anthropic": (
        re.compile(r"^claude-[a-z0-9]+(-[a-z0-9]+)*$"),
        "anthropic ids are hyphen-separated with no dots "
        "(e.g. claude-sonnet-5, claude-opus-4-8)",
    ),
}

_REQUIRED_FIELDS = ("provider", "id", "label")


def id_format_errors(provider: str, model_id: str) -> list[str]:
    """Return format complaints about `model_id`. Empty list means valid."""
    errors: list[str] = []
    if not _UNIVERSAL.match(model_id or ""):
        errors.append(
            f"{provider}/{model_id!r}: must be lowercase, start with a letter or "
            f"digit, and contain no whitespace"
        )
        return errors  # a junk id fails everything else too; one message is enough

    rule = _PROVIDER_RULES.get(provider)
    if rule is not None:
        pattern, explanation = rule
        if not pattern.match(model_id):
            errors.append(f"{provider}/{model_id!r}: {explanation}")
    return errors


def validate_catalog(models: list[dict], known: dict[str, list[str]]) -> list[str]:
    """Validate catalog entries against required fields, format, and a known list."""
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()

    for entry in models:
        missing = [f for f in _REQUIRED_FIELDS if not entry.get(f)]
        if missing:
            errors.append(f"entry {entry!r} is missing required field(s): {', '.join(missing)}")
            continue

        provider, model_id = entry["provider"], entry["id"]

        key = (provider, model_id)
        if key in seen:
            errors.append(f"{provider}/{model_id}: duplicate catalog entry")
        seen.add(key)

        errors.extend(id_format_errors(provider, model_id))

        known_ids = known.get(provider)
        if known_ids is None:
            errors.append(f"{provider}/{model_id}: no known-model list for provider {provider!r}")
        elif model_id not in known_ids:
            errors.append(
                f"{provider}/{model_id}: not in the known-model list for {provider} "
                f"— typo, or the model was retired"
            )

    return errors
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_catalog_rules.py -v`
Expected: PASS — 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/catalog_rules.py tests/test_catalog_rules.py
git commit -m "feat(catalog): per-provider id format rules and catalog validation"
```

---

### Task 2: Wire validation to the real catalog — exposes the malformed ids

This task is expected to produce a **failing** test against the current catalog. That failure is the bug the spec exists to catch. Task 3 fixes it.

**Files:**
- Create: `tests/fixtures/known_models.json`
- Modify: `tests/test_catalog_rules.py` (append)

**Interfaces:**
- Consumes: `id_format_errors`, `validate_catalog` from Task 1; `src.catalog.MODELS`.
- Produces: `tests/fixtures/known_models.json` — a `{provider: [id, ...]}` map that Task 5's diff engine also reads as its offline baseline.

- [ ] **Step 1: Create the known-model fixture**

Create `tests/fixtures/known_models.json`. Anthropic and Groq ids are confirmed; OpenAI holds only the currently-catalogued `gpt-5.5` until the GPT-5.6 ids are confirmed from the provider (see "Deferred" at the end of this plan):

```json
{
  "_comment": "Hand-maintained known-good model ids per provider. Updated as part of the adoption checklist in the spec. The weekly job validates this fixture against live provider lists and flags divergence.",
  "anthropic": [
    "claude-fable-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-haiku-4-5"
  ],
  "openai": [
    "gpt-5.5"
  ],
  "groq": [
    "llama-3.3-70b"
  ]
}
```

- [ ] **Step 2: Write the failing test against the live catalog**

Append to `tests/test_catalog_rules.py`:

```python
import json
from pathlib import Path

KNOWN_MODELS_PATH = Path(__file__).parent / "fixtures" / "known_models.json"


def _load_known() -> dict[str, list[str]]:
    raw = json.loads(KNOWN_MODELS_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def test_known_models_fixture_is_wellformed():
    known = _load_known()
    assert known, "fixture must list at least one provider"
    for provider, ids in known.items():
        assert isinstance(ids, list) and ids, f"{provider} must have a non-empty id list"
        for model_id in ids:
            assert id_format_errors(provider, model_id) == [], (
                f"fixture itself contains a malformed id: {provider}/{model_id}"
            )


def test_live_catalog_is_valid():
    from src.catalog import MODELS

    errors = validate_catalog(MODELS, _load_known())
    assert errors == [], "catalog validation failed:\n" + "\n".join(errors)
```

- [ ] **Step 3: Run the test and confirm it fails on the real defect**

Run: `python -m pytest tests/test_catalog_rules.py::test_live_catalog_is_valid -v`
Expected: FAIL. The output must name both malformed Anthropic ids — `claude-sonnet-4.6` and `claude-opus-4.7` — each flagged twice (format rule + absent from known list). If the failure message says anything else, stop and investigate before proceeding.

- [ ] **Step 4: Commit the failing test**

Commit the test and fixture *before* the fix, so the defect is recorded in history rather than silently corrected.

```bash
git add tests/fixtures/known_models.json tests/test_catalog_rules.py
git commit -m "test(catalog): assert live catalog ids are valid (currently failing)

The two Anthropic entries use dots where provider ids use hyphens:
claude-sonnet-4.6 / claude-opus-4.7. 03-install-profile.sh writes the
catalog string straight into Hermes model.default, so these reach the
provider verbatim and 404. Latent today because the boxes run
openai-codex/gpt-5.5, but both are offered in the dashboard picker.

Fix follows in the next commit."
```

---

### Task 3: Fix the malformed ids and refresh the Anthropic line

**Files:**
- Modify: `src/catalog.py:1-6`

**Interfaces:**
- Consumes: the failing test from Task 2.
- Produces: a `MODELS` literal whose every id passes `validate_catalog`.

- [ ] **Step 1: Replace the Anthropic entries**

In `src/catalog.py`, replace the `MODELS` list. The two dotted ids are corrected and the Anthropic line is brought current; `gpt-5.5` and `llama-3.3-70b` are unchanged pending confirmation of the GPT-5.6 ids:

```python
MODELS = [
    # Anthropic ids are hyphen-separated. See tests/test_catalog_rules.py —
    # a dotted id (the previous claude-sonnet-4.6 / claude-opus-4.7) 404s at
    # the provider because 03-install-profile.sh writes this string verbatim
    # into Hermes model.default.
    {"provider": "anthropic", "id": "claude-opus-5",    "label": "Claude Opus 5"},
    {"provider": "anthropic", "id": "claude-sonnet-5",  "label": "Claude Sonnet 5"},
    {"provider": "anthropic", "id": "claude-haiku-4-5", "label": "Claude Haiku 4.5"},
    {"provider": "openai",    "id": "gpt-5.5",          "label": "GPT-5.5"},
    {"provider": "groq",      "id": "llama-3.3-70b",    "label": "Llama 3.3 70B (Groq)"},
]
```

Note: `claude-fable-5` is deliberately **not** offered. Per the spec it requires 30-day data retention and 400s every request from a zero-data-retention org, cannot have thinking disabled, and can run multi-minute turns — it is not a safe default picker option.

- [ ] **Step 2: Run the previously-failing test**

Run: `python -m pytest tests/test_catalog_rules.py -v`
Expected: PASS — all tests including `test_live_catalog_is_valid`

- [ ] **Step 3: Run the existing catalog API test for regressions**

Run: `python -m pytest tests/test_api_catalog.py -v`
Expected: PASS — the endpoint shape is unchanged

- [ ] **Step 4: Commit**

```bash
git add src/catalog.py
git commit -m "fix(catalog): correct malformed Anthropic model ids

claude-sonnet-4.6 -> the hyphenated current line. Provider ids use
hyphens; the dotted forms would 404. Also drops the retired 4.x entries
in favour of Opus 5 / Sonnet 5 / Haiku 4.5.

Fable 5 is intentionally not offered: it requires 30-day data retention
(400s on ZDR orgs), cannot have thinking disabled, and can run
multi-minute turns."
```

---

### Task 4: Result and diff types

**Files:**
- Create: `src/catalog_check/__init__.py`
- Create: `src/catalog_check/types.py`

**Interfaces:**
- Produces:
  - `ProviderResult(provider: str, model_ids: frozenset[str] | None, mechanism: str, detail: str = "")` — `model_ids is None` means the provider could not be checked.
  - `Diff(unknown: list[tuple[str, str]], new: list[tuple[str, str]], unverifiable: list[ProviderResult], stale: list[tuple[str, str, str]])`
  - `Diff.has_blocking_findings` → `bool`, True when `unknown` is non-empty.
  - `MECHANISM_SCRAPE = "scrape"`, `MECHANISM_API = "api"`, `MECHANISM_NONE = "none"`

- [ ] **Step 1: Write the failing test**

Create `tests/test_catalog_diff.py`:

```python
from src.catalog_check.types import Diff, ProviderResult


def test_provider_result_none_ids_means_unavailable():
    r = ProviderResult(provider="openai", model_ids=None, mechanism="none", detail="no key")
    assert r.model_ids is None
    assert r.detail == "no key"


def test_diff_blocking_only_on_unknown():
    clean = Diff(unknown=[], new=[("openai", "gpt-9")], unverifiable=[], stale=[])
    assert clean.has_blocking_findings is False

    broken = Diff(unknown=[("anthropic", "claude-x")], new=[], unverifiable=[], stale=[])
    assert broken.has_blocking_findings is True


def test_diff_unverifiable_alone_is_not_blocking():
    d = Diff(
        unknown=[],
        new=[],
        unverifiable=[ProviderResult("groq", None, "none", "unreachable")],
        stale=[],
    )
    assert d.has_blocking_findings is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_catalog_diff.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.catalog_check'`

- [ ] **Step 3: Write minimal implementation**

Create `src/catalog_check/__init__.py` (empty file):

```python
```

Create `src/catalog_check/types.py`:

```python
"""Shared result vocabulary for the catalog freshness check.

`ProviderResult.model_ids is None` is the load-bearing distinction: it means
"we could not check this provider", which is reported as unverifiable rather
than being treated as an empty model list. An empty frozenset would wrongly
mark every catalog id as retired.
"""
from dataclasses import dataclass, field

MECHANISM_SCRAPE = "scrape"
MECHANISM_API = "api"
MECHANISM_NONE = "none"


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    model_ids: frozenset[str] | None
    mechanism: str
    detail: str = ""

    @property
    def available(self) -> bool:
        return self.model_ids is not None


@dataclass
class Diff:
    #: (provider, id) present in the catalog but absent from the provider list
    unknown: list[tuple[str, str]] = field(default_factory=list)
    #: (provider, id) offered by the provider but absent from the catalog
    new: list[tuple[str, str]] = field(default_factory=list)
    #: providers that could not be checked at all
    unverifiable: list[ProviderResult] = field(default_factory=list)
    #: (provider, id, verified_at) entries not human-reviewed recently enough
    stale: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def has_blocking_findings(self) -> bool:
        """Only an unknown id fails the run. A new model must not turn CI red."""
        return bool(self.unknown)

    @property
    def is_empty(self) -> bool:
        return not (self.unknown or self.new or self.unverifiable or self.stale)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_catalog_diff.py -v`
Expected: PASS — 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/catalog_check/__init__.py src/catalog_check/types.py
git commit -m "feat(catalog-check): result and diff types"
```

---

### Task 5: Diff engine

**Files:**
- Create: `src/catalog_check/diff.py`
- Modify: `tests/test_catalog_diff.py` (append)

**Interfaces:**
- Consumes: `ProviderResult`, `Diff` from Task 4.
- Produces: `compute_diff(models: list[dict], results: list[ProviderResult], today: date, stale_after_days: int = 365) -> Diff`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_catalog_diff.py`:

```python
from datetime import date

import pytest

from src.catalog_check.diff import compute_diff

TODAY = date(2026, 7, 29)


def _catalog(*entries):
    return [
        {"provider": p, "id": i, "label": i, **extra}
        for p, i, extra in entries
    ]


def test_catalog_id_absent_from_provider_is_unknown():
    models = _catalog(("openai", "gpt-old", {}))
    results = [ProviderResult("openai", frozenset({"gpt-5.5"}), "scrape")]

    d = compute_diff(models, results, TODAY)

    assert d.unknown == [("openai", "gpt-old")]
    assert d.has_blocking_findings is True


def test_provider_model_absent_from_catalog_is_new():
    models = _catalog(("openai", "gpt-5.5", {}))
    results = [ProviderResult("openai", frozenset({"gpt-5.5", "gpt-5.6-luna"}), "scrape")]

    d = compute_diff(models, results, TODAY)

    assert d.new == [("openai", "gpt-5.6-luna")]
    assert d.unknown == []
    assert d.has_blocking_findings is False


def test_unavailable_provider_produces_unverifiable_not_unknown():
    # The critical case: a provider we could not reach must NOT mark its
    # catalog ids as retired.
    models = _catalog(("groq", "llama-3.3-70b", {}))
    results = [ProviderResult("groq", None, "none", "no scraper configured")]

    d = compute_diff(models, results, TODAY)

    assert d.unknown == []
    assert d.new == []
    assert [r.provider for r in d.unverifiable] == ["groq"]
    assert d.has_blocking_findings is False


def test_provider_in_catalog_with_no_result_at_all_is_unverifiable():
    # A provider silently dropped from the fetch list must still be reported.
    models = _catalog(("groq", "llama-3.3-70b", {}))

    d = compute_diff(models, [], TODAY)

    assert [r.provider for r in d.unverifiable] == ["groq"]
    assert d.unknown == []


def test_stale_verified_at_is_flagged():
    models = _catalog(("openai", "gpt-5.5", {"verified_at": "2025-01-01"}))
    results = [ProviderResult("openai", frozenset({"gpt-5.5"}), "scrape")]

    d = compute_diff(models, results, TODAY, stale_after_days=365)

    assert d.stale == [("openai", "gpt-5.5", "2025-01-01")]
    assert d.has_blocking_findings is False


def test_recent_verified_at_is_not_flagged():
    models = _catalog(("openai", "gpt-5.5", {"verified_at": "2026-07-01"}))
    results = [ProviderResult("openai", frozenset({"gpt-5.5"}), "scrape")]

    d = compute_diff(models, results, TODAY, stale_after_days=365)

    assert d.stale == []


def test_missing_verified_at_is_flagged_as_never():
    models = _catalog(("openai", "gpt-5.5", {}))
    results = [ProviderResult("openai", frozenset({"gpt-5.5"}), "scrape")]

    d = compute_diff(models, results, TODAY)

    assert d.stale == [("openai", "gpt-5.5", "never")]


def test_unparseable_verified_at_is_flagged_as_never():
    models = _catalog(("openai", "gpt-5.5", {"verified_at": "last Tuesday"}))
    results = [ProviderResult("openai", frozenset({"gpt-5.5"}), "scrape")]

    d = compute_diff(models, results, TODAY)

    assert d.stale == [("openai", "gpt-5.5", "never")]


def test_clean_catalog_produces_empty_diff():
    models = _catalog(("openai", "gpt-5.5", {"verified_at": "2026-07-01"}))
    results = [ProviderResult("openai", frozenset({"gpt-5.5"}), "scrape")]

    d = compute_diff(models, results, TODAY)

    assert d.is_empty is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_catalog_diff.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.catalog_check.diff'`

- [ ] **Step 3: Write minimal implementation**

Create `src/catalog_check/diff.py`:

```python
"""Pure diff between the catalog and provider model lists.

No I/O. Provider lists arrive as ProviderResult objects from whatever
mechanism fetched them, so this module is identical under scraping and under
an authenticated API.
"""
from datetime import date, datetime

from src.catalog_check.types import Diff, ProviderResult

_NEVER = "never"


def _parse_verified_at(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def compute_diff(
    models: list[dict],
    results: list[ProviderResult],
    today: date,
    stale_after_days: int = 365,
) -> Diff:
    """Compare catalog entries against fetched provider model lists."""
    by_provider = {r.provider: r for r in results}
    diff = Diff()

    # Any provider the catalog references but no result covers is unverifiable.
    # Reported explicitly so a silently-dropped provider cannot read as "clean".
    catalog_providers = {m["provider"] for m in models}
    for provider in sorted(catalog_providers - by_provider.keys()):
        diff.unverifiable.append(
            ProviderResult(provider, None, "none", "no fetch result for provider")
        )

    for result in results:
        if not result.available:
            diff.unverifiable.append(result)

    for entry in models:
        provider, model_id = entry["provider"], entry["id"]

        result = by_provider.get(provider)
        if result is not None and result.available and model_id not in result.model_ids:
            diff.unknown.append((provider, model_id))

        verified = _parse_verified_at(entry.get("verified_at"))
        if verified is None:
            diff.stale.append((provider, model_id, _NEVER))
        elif (today - verified).days > stale_after_days:
            diff.stale.append((provider, model_id, verified.isoformat()))

    catalogued = {(m["provider"], m["id"]) for m in models}
    for result in results:
        if not result.available:
            continue
        for model_id in sorted(result.model_ids):
            if (result.provider, model_id) not in catalogued:
                diff.new.append((result.provider, model_id))

    return diff
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_catalog_diff.py -v`
Expected: PASS — 12 passed

- [ ] **Step 5: Commit**

```bash
git add src/catalog_check/diff.py tests/test_catalog_diff.py
git commit -m "feat(catalog-check): diff engine

An unreachable provider yields unverifiable, never unknown -- treating a
failed fetch as an empty model list would mark every catalog id retired."
```

---

### Task 6: Provider scrapers with a minimum-count guard

**Files:**
- Create: `src/catalog_check/providers.py`
- Test: `tests/test_catalog_providers.py`

**Interfaces:**
- Consumes: `ProviderResult`, `MECHANISM_SCRAPE`, `MECHANISM_NONE` from Task 4.
- Produces:
  - `ScrapeConfig(provider: str, url: str, pattern: str, min_models: int)`
  - `SCRAPE_CONFIGS: list[ScrapeConfig]`
  - `scrape_provider(config: ScrapeConfig, fetch: Callable[[str], str]) -> ProviderResult`
  - `fetch_all(configs: list[ScrapeConfig], fetch: Callable[[str], str]) -> list[ProviderResult]`
  - `http_fetch(url: str) -> str` — the real `httpx` fetcher, used only by `__main__`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_catalog_providers.py`:

```python
import pytest

from src.catalog_check.providers import ScrapeConfig, fetch_all, scrape_provider

CONFIG = ScrapeConfig(
    provider="anthropic",
    url="https://example.test/models",
    pattern=r"claude-[a-z0-9\-]+",
    min_models=2,
)


def test_scrape_extracts_ids():
    page = "Models: claude-opus-5 and claude-sonnet-5 are available."

    result = scrape_provider(CONFIG, fetch=lambda url: page)

    assert result.available is True
    assert result.model_ids == frozenset({"claude-opus-5", "claude-sonnet-5"})
    assert result.mechanism == "scrape"


def test_scrape_below_min_models_is_unavailable():
    # A layout change that breaks extraction must not look like mass retirement.
    page = "Models: claude-opus-5 only."

    result = scrape_provider(CONFIG, fetch=lambda url: page)

    assert result.available is False
    assert "1" in result.detail and "2" in result.detail


def test_scrape_finding_nothing_is_unavailable():
    result = scrape_provider(CONFIG, fetch=lambda url: "<html>nope</html>")

    assert result.available is False


def test_scrape_fetch_exception_is_unavailable_not_raised():
    def boom(url):
        raise RuntimeError("connection reset")

    result = scrape_provider(CONFIG, fetch=boom)

    assert result.available is False
    assert "connection reset" in result.detail


def test_fetch_all_isolates_one_failing_provider():
    good = ScrapeConfig("groq", "https://example.test/g", r"llama-[a-z0-9.\-]+", 1)
    bad = ScrapeConfig("openai", "https://example.test/o", r"gpt-[a-z0-9.\-]+", 1)

    def fetch(url):
        if url.endswith("/o"):
            raise RuntimeError("403")
        return "llama-3.3-70b"

    results = fetch_all([good, bad], fetch=fetch)

    by_provider = {r.provider: r for r in results}
    assert by_provider["groq"].available is True
    assert by_provider["openai"].available is False
    assert len(results) == 2


def test_real_configs_cover_every_catalog_provider():
    from src.catalog import MODELS
    from src.catalog_check.providers import SCRAPE_CONFIGS

    configured = {c.provider for c in SCRAPE_CONFIGS}
    catalogued = {m["provider"] for m in MODELS}
    assert catalogued <= configured, (
        f"no scraper configured for: {sorted(catalogued - configured)}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_catalog_providers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.catalog_check.providers'`

- [ ] **Step 3: Write minimal implementation**

Create `src/catalog_check/providers.py`:

```python
"""Unauthenticated docs scraping for provider model lists.

Scraping is the PRIMARY mechanism, not a fallback. OAuth access tokens are
short-lived and refresh interactively, so an unattended weekly CI job cannot
hold provider credentials; an unauthenticated page fetch works indefinitely.

The minimum-model guard is the important part. A vendor layout change that
breaks extraction would otherwise return a near-empty set and mark the whole
catalog retired. Below `min_models` we report unavailable instead.
"""
from dataclasses import dataclass
import re
from typing import Callable

import httpx

from src.catalog_check.types import (
    MECHANISM_NONE,
    MECHANISM_SCRAPE,
    ProviderResult,
)

_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class ScrapeConfig:
    provider: str
    url: str
    pattern: str
    min_models: int


#: One entry per provider appearing in src.catalog.MODELS.
#: `min_models` is set below the count we expect to find, so it catches a
#: broken scrape without tripping on a single retirement.
SCRAPE_CONFIGS: list[ScrapeConfig] = [
    ScrapeConfig(
        provider="anthropic",
        url="https://platform.claude.com/docs/en/about-claude/models/overview.md",
        pattern=r"claude-[a-z]+-[a-z0-9\-]+",
        min_models=4,
    ),
    ScrapeConfig(
        provider="openai",
        url="https://platform.openai.com/docs/models",
        pattern=r"gpt-[0-9]+(?:\.[0-9]+)*(?:-[a-z]+)*",
        min_models=2,
    ),
    ScrapeConfig(
        provider="groq",
        url="https://console.groq.com/docs/models",
        pattern=r"llama-[0-9]+(?:\.[0-9]+)*-[0-9]+b",
        min_models=1,
    ),
]


def http_fetch(url: str) -> str:
    """Real fetcher. Injected only by __main__ so tests stay offline."""
    response = httpx.get(url, timeout=_TIMEOUT_SECONDS, follow_redirects=True)
    response.raise_for_status()
    return response.text


def scrape_provider(
    config: ScrapeConfig, fetch: Callable[[str], str]
) -> ProviderResult:
    """Fetch and extract model ids. Never raises."""
    try:
        body = fetch(config.url)
    except Exception as exc:  # noqa: BLE001 — any failure is "unverifiable"
        return ProviderResult(
            config.provider, None, MECHANISM_NONE, f"fetch failed: {exc}"
        )

    found = frozenset(m.group(0) for m in re.finditer(config.pattern, body))

    if len(found) < config.min_models:
        return ProviderResult(
            config.provider,
            None,
            MECHANISM_NONE,
            f"extracted {len(found)} models, expected at least "
            f"{config.min_models} — page layout may have changed",
        )

    return ProviderResult(config.provider, found, MECHANISM_SCRAPE)


def fetch_all(
    configs: list[ScrapeConfig], fetch: Callable[[str], str]
) -> list[ProviderResult]:
    """Scrape every configured provider. One failure never blocks the others."""
    return [scrape_provider(config, fetch) for config in configs]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_catalog_providers.py -v`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/catalog_check/providers.py tests/test_catalog_providers.py
git commit -m "feat(catalog-check): unauthenticated docs scrapers

Scraping is primary, not fallback: OAuth tokens refresh interactively and
cannot be held by an unattended weekly job. The min_models guard stops a
broken scrape from reading as mass retirement."
```

---

### Task 7: File sink

**Files:**
- Create: `src/catalog_check/sinks.py`
- Test: `tests/test_catalog_sinks.py`

**Interfaces:**
- Consumes: `Diff`, `ProviderResult` from Task 4.
- Produces:
  - `render_report(diff: Diff, today: date, mechanisms: dict[str, str]) -> str`
  - `write_file_sink(report: str, root: Path, today: date) -> list[Path]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_catalog_sinks.py`:

```python
from datetime import date
from pathlib import Path

from src.catalog_check.sinks import render_report, write_file_sink
from src.catalog_check.types import Diff, ProviderResult

TODAY = date(2026, 7, 29)


def test_report_names_unknown_ids_first():
    diff = Diff(unknown=[("anthropic", "claude-bogus")], new=[("openai", "gpt-5.6-luna")])

    report = render_report(diff, TODAY, {"anthropic": "scrape"})

    assert "claude-bogus" in report
    assert report.index("claude-bogus") < report.index("gpt-5.6-luna")


def test_report_includes_adoption_checklist_for_each_new_model():
    diff = Diff(new=[("openai", "gpt-5.6-luna")])

    report = render_report(diff, TODAY, {})

    assert "gpt-5.6-luna" in report
    assert "speed" in report.lower()
    assert "price" in report.lower()


def test_report_states_mechanism_per_provider():
    report = render_report(Diff(), TODAY, {"anthropic": "scrape", "openai": "none"})

    assert "anthropic" in report and "scrape" in report
    assert "openai" in report and "none" in report


def test_report_names_unverifiable_providers():
    diff = Diff(unverifiable=[ProviderResult("groq", None, "none", "403 forbidden")])

    report = render_report(diff, TODAY, {})

    assert "groq" in report
    assert "403 forbidden" in report


def test_report_on_clean_run_says_so():
    report = render_report(Diff(), TODAY, {"openai": "scrape"})

    assert "no drift" in report.lower()


def test_file_sink_writes_latest_and_dated_copy(tmp_path):
    paths = write_file_sink("# report body", tmp_path, TODAY)

    latest = tmp_path / "latest.md"
    dated = tmp_path / "history" / "2026-07-29.md"

    assert set(paths) == {latest, dated}
    assert latest.read_text(encoding="utf-8") == "# report body"
    assert dated.read_text(encoding="utf-8") == "# report body"


def test_file_sink_overwrites_latest_on_rerun(tmp_path):
    write_file_sink("first", tmp_path, TODAY)
    write_file_sink("second", tmp_path, TODAY)

    assert (tmp_path / "latest.md").read_text(encoding="utf-8") == "second"


def test_file_sink_creates_missing_directories(tmp_path):
    root = tmp_path / "does" / "not" / "exist"

    write_file_sink("body", root, TODAY)

    assert (root / "latest.md").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_catalog_sinks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.catalog_check.sinks'`

- [ ] **Step 3: Write minimal implementation**

Create `src/catalog_check/sinks.py`:

```python
"""Report rendering and sinks.

The file sink is unconditional — it is the record, it is version-controlled,
and it needs no external configuration. The Linear sink (added separately) is
an optional adapter; the check must never fail or go quiet because Linear is
not in use on a given instance.
"""
from datetime import date
from pathlib import Path

from src.catalog_check.types import Diff

_ADOPTION_CHECKLIST = [
    "Id is well-formed and present in the provider's live list",
    "Price recorded (input / output per MTok)",
    "Auth path confirmed — API key vs OAuth",
    "Thinking default, and whether disabling it is accepted",
    "Assistant prefill supported",
    "Sampling parameters accepted",
    "Data-retention or residency requirement",
    "Context window and max output recorded",
    "Speed / cost class assigned",
    "Long-context pricing threshold and multipliers recorded",
    "Which providers serve it",
]


def render_report(diff: Diff, today: date, mechanisms: dict[str, str]) -> str:
    """Render the run as markdown. Unknown ids lead, because they are the only
    category that means a customer-visible dead option."""
    lines = [
        f"# Model catalog check — {today.isoformat()}",
        "",
    ]

    if diff.is_empty:
        lines += ["No drift detected.", ""]

    if diff.unknown:
        lines += [
            "## Unknown ids — BLOCKING",
            "",
            "Present in the catalog, absent from the provider. Either a typo or a "
            "retirement. These are offered in the dashboard picker today.",
            "",
        ]
        lines += [f"- `{provider}` / `{model_id}`" for provider, model_id in diff.unknown]
        lines.append("")

    if diff.new:
        lines += [
            "## New models available",
            "",
            "Not adopted automatically. Work the checklist per model, then edit "
            "`src/catalog.py` and `tests/fixtures/known_models.json` by hand.",
            "",
        ]
        for provider, model_id in diff.new:
            lines.append(f"### `{provider}` / `{model_id}`")
            lines.append("")
            lines += [f"- [ ] {item}" for item in _ADOPTION_CHECKLIST]
            lines.append("")

    if diff.unverifiable:
        lines += [
            "## Unverifiable providers",
            "",
            "Could not be checked. This is NOT the same as no changes — treat a "
            "repeat appearance here as a broken scraper.",
            "",
        ]
        lines += [
            f"- `{r.provider}`: {r.detail or 'no detail'}" for r in diff.unverifiable
        ]
        lines.append("")

    if diff.stale:
        lines += [
            "## Entries overdue for human review",
            "",
        ]
        lines += [
            f"- `{provider}` / `{model_id}` — last verified: {verified}"
            for provider, model_id, verified in diff.stale
        ]
        lines.append("")

    lines += ["## Fetch mechanism per provider", ""]
    if mechanisms:
        lines += [
            f"- `{provider}`: {mechanism}"
            for provider, mechanism in sorted(mechanisms.items())
        ]
    else:
        lines.append("- (none recorded)")
    lines.append("")

    return "\n".join(lines)


def write_file_sink(report: str, root: Path, today: date) -> list[Path]:
    """Write `latest.md` plus a dated copy. Always runs."""
    history = root / "history"
    history.mkdir(parents=True, exist_ok=True)

    latest = root / "latest.md"
    dated = history / f"{today.isoformat()}.md"

    for path in (latest, dated):
        path.write_text(report, encoding="utf-8")

    return [latest, dated]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_catalog_sinks.py -v`
Expected: PASS — 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/catalog_check/sinks.py tests/test_catalog_sinks.py
git commit -m "feat(catalog-check): report rendering and unconditional file sink"
```

---

### Task 8: Schema extension — metadata fields on catalog entries

**Files:**
- Modify: `src/catalog.py`
- Test: `tests/test_catalog_rules.py` (append)

**Interfaces:**
- Consumes: `validate_catalog` from Task 1.
- Produces: `MODELS` entries carrying `speed_class`, `price_in`, `price_out`, `long_context_threshold`, `verified_at`. `list_models()` return shape unchanged for existing consumers (extra keys only).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_catalog_rules.py`:

```python
def test_every_entry_has_metadata_fields():
    from src.catalog import MODELS

    for entry in MODELS:
        assert entry.get("speed_class") in ("fast", "heavy"), entry
        assert isinstance(entry.get("price_in"), (int, float)), entry
        assert isinstance(entry.get("price_out"), (int, float)), entry
        assert isinstance(entry.get("verified_at"), str), entry


def test_long_context_threshold_shape_when_present():
    from src.catalog import MODELS

    for entry in MODELS:
        threshold = entry.get("long_context_threshold")
        if threshold is None:
            continue
        assert set(threshold) == {"tokens", "input_multiplier", "output_multiplier"}, entry
        assert threshold["tokens"] > 0


def test_list_models_preserves_existing_consumer_contract():
    from src.catalog import list_models

    for entry in list_models():
        assert "id" in entry and "provider" in entry and "label" in entry
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_catalog_rules.py -v`
Expected: FAIL — `test_every_entry_has_metadata_fields`, `AssertionError` because `speed_class` is absent

- [ ] **Step 3: Write minimal implementation**

Replace `MODELS` in `src/catalog.py`:

```python
# speed_class gates synchronous agent-to-agent consults (see the switchable
# dispatch spec): `fast` peers may be consulted inline, `heavy` peers are
# assign-only. long_context_threshold exists because speed class alone does not
# bound cost — a cheap peer becomes expensive above the threshold.
MODELS = [
    {
        "provider": "anthropic", "id": "claude-opus-5", "label": "Claude Opus 5",
        "speed_class": "heavy", "price_in": 5.00, "price_out": 25.00,
        "long_context_threshold": None, "verified_at": "2026-07-29",
    },
    {
        "provider": "anthropic", "id": "claude-sonnet-5", "label": "Claude Sonnet 5",
        "speed_class": "fast", "price_in": 3.00, "price_out": 15.00,
        "long_context_threshold": None, "verified_at": "2026-07-29",
    },
    {
        "provider": "anthropic", "id": "claude-haiku-4-5", "label": "Claude Haiku 4.5",
        "speed_class": "fast", "price_in": 1.00, "price_out": 5.00,
        "long_context_threshold": None, "verified_at": "2026-07-29",
    },
    {
        "provider": "openai", "id": "gpt-5.5", "label": "GPT-5.5",
        "speed_class": "fast", "price_in": 1.25, "price_out": 10.00,
        "long_context_threshold": None, "verified_at": "never",
    },
    {
        "provider": "groq", "id": "llama-3.3-70b", "label": "Llama 3.3 70B (Groq)",
        "speed_class": "fast", "price_in": 0.59, "price_out": 0.79,
        "long_context_threshold": None, "verified_at": "never",
    },
]
```

> **Reviewer note.** `gpt-5.5` and `llama-3.3-70b` carry `verified_at: "never"` deliberately — their prices are unconfirmed placeholders and the weekly run will flag both as overdue for review until a human checks them. That is the intended behavior, not an oversight. Do not substitute guessed prices to silence the finding.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_catalog_rules.py tests/test_api_catalog.py -v`
Expected: PASS — all tests, including the unchanged API contract test

- [ ] **Step 5: Commit**

```bash
git add src/catalog.py tests/test_catalog_rules.py
git commit -m "feat(catalog): speed_class, pricing, and verified_at metadata

Additive fields; list_models() keeps its shape for ModelPicker. speed_class
is introduced here so the dispatch work consumes an existing field rather
than adding one. Unconfirmed prices carry verified_at=never so the weekly
run flags them rather than presenting a guess as fact."
```

---

### Task 9: Escalation state — two consecutive unverifiable runs

**Files:**
- Create: `src/catalog_check/state.py`
- Test: `tests/test_catalog_state.py`

**Interfaces:**
- Consumes: `Diff` from Task 4.
- Produces:
  - `load_state(path: Path) -> dict[str, int]`
  - `update_state(previous: dict[str, int], diff: Diff) -> dict[str, int]`
  - `escalations(state: dict[str, int], threshold: int = 2) -> list[str]`
  - `save_state(path: Path, state: dict[str, int]) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_catalog_state.py`:

```python
import json
from pathlib import Path

from src.catalog_check.state import escalations, load_state, save_state, update_state
from src.catalog_check.types import Diff, ProviderResult


def _unverifiable(*providers):
    return Diff(unverifiable=[ProviderResult(p, None, "none", "x") for p in providers])


def test_load_state_missing_file_returns_empty(tmp_path):
    assert load_state(tmp_path / "nope.json") == {}


def test_load_state_corrupt_file_returns_empty(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("not json", encoding="utf-8")

    assert load_state(path) == {}


def test_first_unverifiable_run_counts_one():
    state = update_state({}, _unverifiable("groq"))

    assert state == {"groq": 1}
    assert escalations(state) == []


def test_second_consecutive_unverifiable_run_escalates():
    state = update_state({"groq": 1}, _unverifiable("groq"))

    assert state == {"groq": 2}
    assert escalations(state) == ["groq"]


def test_successful_run_clears_the_counter():
    state = update_state({"groq": 5}, Diff())

    assert state == {}
    assert escalations(state) == []


def test_one_provider_recovering_does_not_clear_another():
    state = update_state({"groq": 1, "openai": 1}, _unverifiable("openai"))

    assert state == {"openai": 2}
    assert escalations(state) == ["openai"]


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "nested" / "state.json"

    save_state(path, {"groq": 2})

    assert load_state(path) == {"groq": 2}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_catalog_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.catalog_check.state'`

- [ ] **Step 3: Write minimal implementation**

Create `src/catalog_check/state.py`:

```python
"""Consecutive-unverifiable counters, persisted between weekly runs.

A provider that cannot be checked once is noise; the same provider twice in a
row is a broken scraper, which is the failure mode that makes the whole check
worse than useless — a green run that verified nothing.
"""
import json
from pathlib import Path

from src.catalog_check.types import Diff


def load_state(path: Path) -> dict[str, int]:
    """Read counters. A missing or unreadable file is an empty state, not an error."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, int)}


def update_state(previous: dict[str, int], diff: Diff) -> dict[str, int]:
    """Increment counters for providers still unverifiable; drop the recovered ones."""
    unverifiable = {r.provider for r in diff.unverifiable}
    return {
        provider: previous.get(provider, 0) + 1
        for provider in sorted(unverifiable)
    }


def escalations(state: dict[str, int], threshold: int = 2) -> list[str]:
    """Providers unverifiable for at least `threshold` consecutive runs."""
    return sorted(p for p, count in state.items() if count >= threshold)


def save_state(path: Path, state: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_catalog_state.py -v`
Expected: PASS — 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/catalog_check/state.py tests/test_catalog_state.py
git commit -m "feat(catalog-check): escalate a provider unverifiable twice running"
```

---

### Task 10: CLI entry point

**Files:**
- Create: `src/catalog_check/__main__.py`
- Modify: `tests/test_catalog_sinks.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 4–9.
- Produces: `run(root: Path, today: date, fetch: Callable[[str], str]) -> int` — returns the process exit code. `main()` wires the real fetcher and calls `sys.exit`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_catalog_sinks.py`:

```python
def test_run_returns_zero_and_writes_report_on_clean_catalog(tmp_path, monkeypatch):
    from src.catalog_check.__main__ import run

    monkeypatch.setattr(
        "src.catalog_check.__main__.SCRAPE_CONFIGS",
        [ScrapeConfig("openai", "https://example.test/o", r"gpt-[0-9.]+", 1)],
    )
    monkeypatch.setattr(
        "src.catalog_check.__main__.MODELS",
        [{"provider": "openai", "id": "gpt-5.5", "label": "GPT-5.5",
          "verified_at": "2026-07-01"}],
    )

    code = run(tmp_path, TODAY, fetch=lambda url: "gpt-5.5")

    assert code == 0
    assert (tmp_path / "latest.md").exists()


def test_run_returns_nonzero_on_unknown_id(tmp_path, monkeypatch):
    from src.catalog_check.__main__ import run

    monkeypatch.setattr(
        "src.catalog_check.__main__.SCRAPE_CONFIGS",
        [ScrapeConfig("openai", "https://example.test/o", r"gpt-[0-9.]+", 1)],
    )
    monkeypatch.setattr(
        "src.catalog_check.__main__.MODELS",
        [{"provider": "openai", "id": "gpt-old", "label": "old",
          "verified_at": "2026-07-01"}],
    )

    code = run(tmp_path, TODAY, fetch=lambda url: "gpt-5.5")

    assert code == 1
    assert "gpt-old" in (tmp_path / "latest.md").read_text(encoding="utf-8")


def test_run_still_writes_report_when_every_provider_fails(tmp_path, monkeypatch):
    from src.catalog_check.__main__ import run

    monkeypatch.setattr(
        "src.catalog_check.__main__.SCRAPE_CONFIGS",
        [ScrapeConfig("openai", "https://example.test/o", r"gpt-[0-9.]+", 1)],
    )
    monkeypatch.setattr(
        "src.catalog_check.__main__.MODELS",
        [{"provider": "openai", "id": "gpt-5.5", "label": "GPT-5.5",
          "verified_at": "2026-07-01"}],
    )

    def boom(url):
        raise RuntimeError("no network")

    code = run(tmp_path, TODAY, fetch=boom)

    report = (tmp_path / "latest.md").read_text(encoding="utf-8")
    assert code == 0  # unverifiable alone is not blocking
    assert "Unverifiable" in report
    assert "no network" in report
```

Add the import at the top of `tests/test_catalog_sinks.py`:

```python
from src.catalog_check.providers import ScrapeConfig
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_catalog_sinks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.catalog_check.__main__'`

- [ ] **Step 3: Write minimal implementation**

Create `src/catalog_check/__main__.py`:

```python
"""CLI: python -m src.catalog_check [--root docs/model-catalog]

Wires fetch -> diff -> report -> sinks and returns an exit code. Exits
non-zero only on an unknown catalog id; a newly shipped model or an
unverifiable provider must not turn the weekly run red.
"""
import argparse
from datetime import date, datetime, timezone
from pathlib import Path
import sys
from typing import Callable

from src.catalog import MODELS
from src.catalog_check.diff import compute_diff
from src.catalog_check.providers import SCRAPE_CONFIGS, fetch_all, http_fetch
from src.catalog_check.sinks import render_report, write_file_sink
from src.catalog_check.state import escalations, load_state, save_state, update_state

DEFAULT_ROOT = Path("docs/model-catalog")
_STATE_FILENAME = "state.json"


def run(root: Path, today: date, fetch: Callable[[str], str]) -> int:
    results = fetch_all(SCRAPE_CONFIGS, fetch=fetch)
    diff = compute_diff(MODELS, results, today)

    mechanisms = {r.provider: r.mechanism for r in results}
    report = render_report(diff, today, mechanisms)

    state_path = root / _STATE_FILENAME
    state = update_state(load_state(state_path), diff)
    repeated = escalations(state)
    if repeated:
        report += "\n".join(
            [
                "## Escalation — unverifiable two runs running",
                "",
                "Treat these as broken scrapers, not quiet providers:",
                "",
                *[f"- `{provider}`" for provider in repeated],
                "",
            ]
        )

    write_file_sink(report, root, today)
    save_state(state_path, state)

    return 1 if diff.has_blocking_findings else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the model catalog for drift.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()

    today = datetime.now(timezone.utc).date()
    sys.exit(run(args.root, today, fetch=http_fetch))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest tests/ -v -k catalog`
Expected: PASS — every catalog test

- [ ] **Step 5: Commit**

```bash
git add src/catalog_check/__main__.py tests/test_catalog_sinks.py
git commit -m "feat(catalog-check): CLI entry point

Exit code is non-zero only on an unknown catalog id. The report is written
even when every provider fetch fails, so a total outage is visible rather
than silent."
```

---

### Task 11: GitHub Actions weekly workflow

**Files:**
- Create: `.github/workflows/model-catalog-check.yml`

**Interfaces:**
- Consumes: `python -m src.catalog_check` from Task 10.
- Produces: a weekly scheduled run that commits the report and fails the job on blocking findings.

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/model-catalog-check.yml`:

```yaml
name: Model catalog check

# Weekly. Vendor launch cadence has been far faster than monthly, and the
# check needs no credentials — it scrapes public docs pages, because OAuth
# tokens cannot be held by an unattended job.
on:
  schedule:
    - cron: "17 6 * * 1"   # Mondays 06:17 UTC — off the hour to avoid runner contention
  workflow_dispatch:

permissions:
  contents: write          # to commit the report
  issues: write            # reserved for the Linear/issue sink (Task 12)

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run the offline validation suite first
        # If the catalog is internally invalid, the drift report is noise.
        run: python -m pytest tests/test_catalog_rules.py -v

      - name: Check catalog against provider docs
        id: check
        continue-on-error: true
        run: python -m src.catalog_check --root docs/model-catalog

      - name: Commit the report
        run: |
          git config user.name  "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add docs/model-catalog
          if git diff --cached --quiet; then
            echo "No report change to commit."
          else
            git commit -m "chore(catalog): weekly model catalog report"
            git push
          fi

      - name: Fail if blocking findings
        if: steps.check.outcome == 'failure'
        run: |
          echo "::error::Catalog contains unknown model ids — see docs/model-catalog/latest.md"
          exit 1
```

Note the ordering: the report is committed **before** the job fails, so a red run still leaves the evidence in the repo. `continue-on-error` on the check step plus the explicit final gate is what buys that.

- [ ] **Step 2: Verify the CLI runs locally exactly as the workflow invokes it**

Run: `python -m src.catalog_check --root docs/model-catalog`
Expected: exits 0 or 1, writes `docs/model-catalog/latest.md` and `docs/model-catalog/history/<today>.md`. Read the report and confirm the mechanism section lists all three providers.

- [ ] **Step 3: Verify the workflow YAML parses**

Run: `python -c "import yaml,pathlib; yaml.safe_load(pathlib.Path('.github/workflows/model-catalog-check.yml').read_text()); print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/model-catalog-check.yml docs/model-catalog
git commit -m "ci(catalog): weekly model catalog freshness check

Scrapes public docs pages, so no credentials are needed -- OAuth tokens
refresh interactively and cannot be held by an unattended job. Commits the
report before failing, so a red run still leaves the evidence behind."
```

- [ ] **Step 5: Confirm Actions is enabled on the repo**

This repo had no `.github/workflows/` before this task, so Actions may be disabled at the org or repo level. After pushing, open the repo's **Actions** tab and trigger the workflow via **Run workflow** (`workflow_dispatch`). If the tab is absent or the run is blocked, stop and report — the fallback is a scheduled Claude Code routine invoking the same `python -m src.catalog_check`, which needs no CI.

---

### Task 12: Linear sink as an optional adapter

**Files:**
- Modify: `src/catalog_check/sinks.py`
- Modify: `src/catalog_check/__main__.py`
- Modify: `tests/test_catalog_sinks.py` (append)

**Interfaces:**
- Consumes: `render_report` output from Task 7; `Diff` from Task 4.
- Produces:
  - `LinearConfig(api_key: str | None, team_id: str | None)` with `LinearConfig.from_env() -> LinearConfig` and `.configured -> bool`
  - `write_linear_sink(report: str, diff: Diff, config: LinearConfig, post: Callable[[str, dict], dict]) -> str | None` — returns the issue identifier, or `None` when unconfigured or on failure.
  - `run_sinks(report: str, diff: Diff, root: Path, today: date, linear: LinearConfig, post: Callable[[str, dict], dict]) -> dict[str, str]` — a `{sink_name: status}` map.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_catalog_sinks.py`:

```python
from src.catalog_check.sinks import LinearConfig, run_sinks, write_linear_sink


def test_linear_sink_skipped_when_unconfigured():
    config = LinearConfig(api_key=None, team_id=None)

    result = write_linear_sink("body", Diff(), config, post=lambda url, payload: {})

    assert result is None


def test_linear_sink_not_called_on_clean_run():
    calls = []

    def post(url, payload):
        calls.append(payload)
        return {"data": {"issueCreate": {"issue": {"identifier": "JNO-1"}}}}

    config = LinearConfig(api_key="k", team_id="t")
    result = write_linear_sink("body", Diff(), config, post=post)

    assert result is None
    assert calls == []


def test_linear_sink_posts_on_drift():
    def post(url, payload):
        return {"data": {"issueCreate": {"issue": {"identifier": "JNO-42"}}}}

    config = LinearConfig(api_key="k", team_id="t")
    diff = Diff(unknown=[("openai", "gpt-old")])

    assert write_linear_sink("body", diff, config, post=post) == "JNO-42"


def test_linear_sink_failure_returns_none_and_does_not_raise():
    def post(url, payload):
        raise RuntimeError("502 bad gateway")

    config = LinearConfig(api_key="k", team_id="t")
    diff = Diff(unknown=[("openai", "gpt-old")])

    assert write_linear_sink("body", diff, config, post=post) is None


def test_run_sinks_writes_file_even_when_linear_raises(tmp_path):
    def post(url, payload):
        raise RuntimeError("502")

    statuses = run_sinks(
        "body",
        Diff(unknown=[("openai", "gpt-old")]),
        tmp_path,
        TODAY,
        LinearConfig(api_key="k", team_id="t"),
        post=post,
    )

    assert (tmp_path / "latest.md").read_text(encoding="utf-8") == "body"
    assert statuses["file"] == "written"
    assert statuses["linear"] == "failed"


def test_run_sinks_reports_linear_as_skipped_when_unconfigured(tmp_path):
    statuses = run_sinks(
        "body", Diff(), tmp_path, TODAY,
        LinearConfig(api_key=None, team_id=None),
        post=lambda url, payload: {},
    )

    assert statuses["file"] == "written"
    assert statuses["linear"] == "skipped (not configured)"


def test_linear_config_from_env_reads_both_vars(monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    monkeypatch.setenv("LINEAR_TEAM_ID", "t")

    config = LinearConfig.from_env()

    assert config.configured is True


def test_linear_config_partial_env_is_not_configured(monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    monkeypatch.delenv("LINEAR_TEAM_ID", raising=False)

    assert LinearConfig.from_env().configured is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_catalog_sinks.py -v`
Expected: FAIL — `ImportError: cannot import name 'LinearConfig'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/catalog_check/sinks.py`:

```python
import os
from dataclasses import dataclass
from typing import Callable

import httpx

_LINEAR_URL = "https://api.linear.app/graphql"
_LINEAR_TIMEOUT = 30.0

_CREATE_ISSUE = """
mutation IssueCreate($teamId: String!, $title: String!, $description: String!) {
  issueCreate(input: {teamId: $teamId, title: $title, description: $description}) {
    issue { identifier }
  }
}
"""


@dataclass(frozen=True)
class LinearConfig:
    api_key: str | None
    team_id: str | None

    @classmethod
    def from_env(cls) -> "LinearConfig":
        return cls(
            api_key=os.environ.get("LINEAR_API_KEY") or None,
            team_id=os.environ.get("LINEAR_TEAM_ID") or None,
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.team_id)


def linear_post(url: str, payload: dict) -> dict:
    """Real Linear transport. Injected only by __main__ so tests stay offline."""
    response = httpx.post(url, json=payload, timeout=_LINEAR_TIMEOUT)
    response.raise_for_status()
    return response.json()


def write_linear_sink(
    report: str,
    diff: Diff,
    config: LinearConfig,
    post: Callable[[str, dict], dict],
) -> str | None:
    """Open an issue when there is drift. Optional: returns None when
    unconfigured, when there is nothing to report, or on any failure. Never
    raises — the file sink is the record and must not be affected."""
    if not config.configured or diff.is_empty:
        return None

    payload = {
        "query": _CREATE_ISSUE,
        "variables": {
            "teamId": config.team_id,
            "title": "Model catalog drift detected",
            "description": report,
        },
    }
    try:
        body = post(_LINEAR_URL, payload)
        return body["data"]["issueCreate"]["issue"]["identifier"]
    except Exception:  # noqa: BLE001 — an optional sink never breaks the run
        return None


def run_sinks(
    report: str,
    diff: Diff,
    root: Path,
    today: date,
    linear: LinearConfig,
    post: Callable[[str, dict], dict],
) -> dict[str, str]:
    """Run every sink. The file sink is unconditional and runs first."""
    statuses: dict[str, str] = {}

    write_file_sink(report, root, today)
    statuses["file"] = "written"

    if not linear.configured:
        statuses["linear"] = "skipped (not configured)"
        return statuses

    identifier = write_linear_sink(report, diff, linear, post=post)
    if identifier:
        statuses["linear"] = f"issue {identifier}"
    elif diff.is_empty:
        statuses["linear"] = "skipped (no drift)"
    else:
        statuses["linear"] = "failed"

    return statuses
```

Also add `from pathlib import Path` to the imports at the top of `sinks.py` if it is not already there.

- [ ] **Step 4: Wire it into the CLI**

In `src/catalog_check/__main__.py`, replace the `write_file_sink(report, root, today)` call with `run_sinks`, and add the imports:

```python
from src.catalog_check.sinks import (
    LinearConfig,
    linear_post,
    render_report,
    run_sinks,
)
```

Replace the sink call in `run()`:

```python
    statuses = run_sinks(
        report, diff, root, today, LinearConfig.from_env(), post=linear_post
    )
    for name, status in sorted(statuses.items()):
        print(f"sink {name}: {status}")
```

Remove the now-unused `write_file_sink` import.

- [ ] **Step 5: Run the full catalog suite**

Run: `python -m pytest tests/ -v -k catalog`
Expected: PASS — every catalog test. Note that `test_run_*` tests from Task 10 still pass because `LinearConfig.from_env()` is unconfigured in the test environment, so the Linear sink is skipped.

- [ ] **Step 6: Commit**

```bash
git add src/catalog_check/sinks.py src/catalog_check/__main__.py tests/test_catalog_sinks.py
git commit -m "feat(catalog-check): optional Linear sink

Gated on LINEAR_API_KEY + LINEAR_TEAM_ID. Reports as 'skipped (not
configured)' rather than erroring, and a Linear failure never prevents the
file sink from writing -- the check must not depend on Linear being in use."
```

---

## Deferred — needs input before it can be executed

### GPT-5.6 catalog entries

**Blocked on:** the literal OpenAI API id strings for the Sol / Terra / Luna tiers. These were not confirmable from public pricing pages, and guessing them would reintroduce exactly the defect Task 3 fixes. Authoritative local sources: the Codex setup in use, or `~/.hermes/config.yaml` on a provisioned box.

Once the ids are known, this is a data-only change — no new code:

1. Add the three ids to `tests/fixtures/known_models.json` under `openai`.
2. Add entries to `MODELS` with the values from the spec's appendix: Sol `$5/$30` `heavy`, Terra `$2.50/$15` `fast`, Luna `$1/$6` `fast`; all three `long_context_threshold` = `{"tokens": 272000, "input_multiplier": 2.0, "output_multiplier": 1.5}`; `verified_at` = the date of confirmation.
3. Decide whether to keep `gpt-5.5` on offer or retire it.
4. Run `python -m pytest tests/test_catalog_rules.py -v`.

**Two open questions to resolve first:** whether **Terra** should be `fast` (my classification is inferred from its "balanced default" positioning, not measured latency — `Luna` only is the conservative choice), and whether **Sol** is actually entitled on the `openai-codex` channel given the June preview restrictions versus the July GA claim.

---

## Self-review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §1 Validation test — highest-value assertion | Tasks 1, 2, 3 |
| §2 Weekly freshness job, four diff categories, exit code | Tasks 5, 10, 11 |
| §3 Report sinks — file unconditional, Linear optional | Tasks 7, 12 |
| §4 Adoption stays manual + checklist | Task 7 (`_ADOPTION_CHECKLIST`) |
| §5 Scrape primary, no API key in CI, mechanism recorded | Tasks 6, 7, 11 |
| §6 Catalog schema extension | Task 8 |
| Risks — scrape fragility, min-count, 2-run escalation | Tasks 6, 9 |
| Risks — fixture drift validated against live list | Task 2 + Task 5 `new` category |
| Testing — all offline, injected fetchers/writers | every task |
| Slice 1 — fix ids, refresh, schema fields | Tasks 3, 8 (+ GPT-5.6 deferred) |

Two spec items intentionally **not** implemented as code: the optional attended API-fetch path (§5 mechanism 2) is left for when a key exists — `compute_diff` already accepts any `ProviderResult` regardless of mechanism, so it needs no change to support it; and the `Changed` category, which is unevaluable under scraping alone and is covered by reporting `unverifiable` plus the mechanism section.

**Placeholder scan:** no TBDs, no "add error handling", no "similar to Task N". Every code step carries runnable code. The one deferred item is explicitly separated with its blocking input named.

**Type consistency:** `ProviderResult` / `Diff` field names are consistent across Tasks 4–12. `compute_diff` signature matches its call in `__main__`. `render_report(diff, today, mechanisms)` matches. `write_file_sink(report, root, today)` matches, and Task 12 supersedes the direct call with `run_sinks` while keeping `write_file_sink`'s own signature and tests intact. `id_format_errors` / `validate_catalog` are used with the same signatures in Tasks 1, 2, and 8. `LinearConfig.configured` is a property in both the implementation and the tests.
