"""Report rendering and sinks.

The file sink is unconditional — it is the record, it is version-controlled,
and it needs no external configuration. The Linear sink (added separately) is
an optional adapter; the check must never fail or go quiet because Linear is
not in use on a given instance.
"""
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

import httpx

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

#: Full adoption checklists are expensive to read. A scrape pattern that
#: over-matches (or a genuine multi-launch week) must not turn the report
#: into dozens of 11-item checklists nobody reads. Cap it; list the rest as
#: one-liners under a "further candidates" heading instead.
_NEW_MODEL_CHECKLIST_CAP = 5


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

    if diff.unlisted:
        lines += [
            "## Not on the checked page — REVIEW, not blocking",
            "",
            "In the catalog, absent from the source we can check. That source "
            "does not enumerate everything the provider serves, so this may be "
            "a retirement or may just be a model reached over a route the page "
            "does not document.",
            "",
            "**Confirm against the surface the boxes actually use before "
            "removing anything.** For openai that is Hermes's `openai-codex` "
            "provider, not `platform.openai.com/docs` — check the model picker "
            "in a profile's dashboard. Deleting a model from `src/catalog.py` "
            "on this finding alone has already dropped a live model once.",
            "",
        ]
        lines += [
            f"- `{provider}` / `{model_id}`" for provider, model_id in diff.unlisted
        ]
        lines.append("")

    if diff.new:
        lines += [
            "## New models available",
            "",
            "Not adopted automatically. Work the checklist per model, then edit "
            "`src/catalog.py` and `tests/fixtures/known_models.json` by hand.",
            "",
        ]
        checklisted = diff.new[:_NEW_MODEL_CHECKLIST_CAP]
        remainder = diff.new[_NEW_MODEL_CHECKLIST_CAP:]
        if remainder:
            lines.append(
                f"{len(diff.new)} new ids found; showing full checklists for the "
                f"first {_NEW_MODEL_CHECKLIST_CAP}."
            )
            lines.append("")
        for provider, model_id in checklisted:
            lines.append(f"### `{provider}` / `{model_id}`")
            lines.append("")
            lines += [f"- [ ] {item}" for item in _ADOPTION_CHECKLIST]
            lines.append("")
        if remainder:
            lines += ["### Further candidates needing triage", ""]
            lines += [
                f"- `{provider}` / `{model_id}`" for provider, model_id in remainder
            ]
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


def make_linear_post(
    api_key: str | None, transport: httpx.BaseTransport | None = None
) -> Callable[[str, dict], dict]:
    """Build the real Linear transport, bound to its credential.

    The credential is captured in the closure rather than accepted as a
    function argument so that a credentialless transport cannot be
    constructed by accident — every caller must supply a key up front.

    `transport` is normally left as None (the real network); tests pass an
    `httpx.MockTransport` to exercise this function's contract (headers,
    timeout, raise-on-error) without touching the network.
    """

    def post(url: str, payload: dict) -> dict:
        with httpx.Client(transport=transport) as client:
            response = client.post(
                url,
                json=payload,
                timeout=_LINEAR_TIMEOUT,
                # Linear personal API keys go in Authorization RAW, with no
                # "Bearer " prefix — that prefix is for OAuth tokens only.
                # This looks wrong next to every other API but is correct.
                headers={
                    "Authorization": api_key or "",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            return response.json()

    return post


def write_linear_sink(
    report: str,
    diff: Diff,
    config: LinearConfig,
    post: Callable[[str, dict], dict],
) -> str | None:
    """Open an issue when there is a blocking finding. Optional: returns None
    when unconfigured or when there is nothing blocking to report; returns
    `"failed: ..."` (with the exception detail) on any transport failure.
    Never raises — the file sink is the record and must not be affected."""
    if not config.configured or not diff.has_blocking_findings:
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
    except Exception as exc:  # noqa: BLE001 — an optional sink never breaks the run
        return f"failed: {exc.__class__.__name__}: {exc}"


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
    if identifier is None:
        statuses["linear"] = "skipped (no blocking findings)"
    elif identifier.startswith("failed:"):
        statuses["linear"] = identifier
    else:
        statuses["linear"] = f"issue {identifier}"

    return statuses
