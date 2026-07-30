"""CLI: python -m src.catalog_check [--root docs/model-catalog]

Wires fetch -> diff -> report -> sinks and returns an exit code. Exits
non-zero only on an unknown catalog id; a newly shipped model or an
unverifiable provider must not turn the weekly run red.

Exit codes: 0 clean, 1 blocking findings (an unknown catalog id), 2 the
check itself failed unexpectedly (bug, I/O error) — distinct from 1 so a
GitHub Actions consumer does not report "unknown model ids" for a crash.
"""
import argparse
from datetime import date, datetime, timezone
from pathlib import Path
import sys
import traceback
from typing import Callable

from src.catalog import MODELS
from src.catalog_check.diff import compute_diff
from src.catalog_check.providers import SCRAPE_CONFIGS, fetch_all, http_fetch
from src.catalog_check.sinks import (
    LinearConfig,
    make_linear_post,
    render_report,
    run_sinks,
)
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
        report += "\n" + "\n".join(
            [
                "## Escalation — unverifiable two runs running",
                "",
                "Treat these as broken scrapers, not quiet providers:",
                "",
                *[f"- `{provider}`" for provider in repeated],
                "",
            ]
        )

    linear_config = LinearConfig.from_env()
    statuses = run_sinks(
        report,
        diff,
        root,
        today,
        linear_config,
        post=make_linear_post(linear_config.api_key),
    )
    for name, status in sorted(statuses.items()):
        print(f"sink {name}: {status}")
    save_state(state_path, state)

    return 1 if diff.has_blocking_findings else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the model catalog for drift.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()

    today = datetime.now(timezone.utc).date()
    try:
        code = run(args.root, today, fetch=http_fetch)
    except Exception:  # noqa: BLE001 — an internal error must exit 2, not crash
        traceback.print_exc(file=sys.stderr)
        code = 2
    sys.exit(code)


if __name__ == "__main__":
    main()
