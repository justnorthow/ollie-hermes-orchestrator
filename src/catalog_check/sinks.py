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
