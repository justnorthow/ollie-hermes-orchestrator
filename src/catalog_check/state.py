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
