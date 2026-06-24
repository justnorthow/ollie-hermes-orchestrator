"""Governance artifact extractors. Each maps an agent run's final output to a
normalized audit record {status, findings, content}. Registered by event_type.

`parse_compliance` is the Python port of the newsletter app's parseCompliance:
it reads the fenced ```compliance block (STATUS + per-issue items) and returns
the edition body with that block stripped."""

import re
from typing import Callable

_BLOCK_RE = re.compile(r"```compliance\s*([\s\S]*?)```", re.IGNORECASE)
_ITEM_START_RE = re.compile(r'^-\s*text:\s*"?(.*?)"?\s*$', re.IGNORECASE)
_FIELD_RE = re.compile(r'^\s+(rule|citation|rewrite):\s*"?(.*?)"?\s*$', re.IGNORECASE)


def _status_of(block: str) -> str:
    m = re.search(r"STATUS:\s*(.+)", block, re.IGNORECASE)
    s = (m.group(1) if m else "").upper()
    if "NEEDS" in s:
        return "needs_review"
    if "FLAG" in s:
        return "flagged"
    if "PASS" in s:
        return "pass"
    return "unknown"


def _parse_items(block: str) -> list[dict]:
    items: list[dict] = []
    cur: dict | None = None
    for raw in block.splitlines():
        line = raw.rstrip()
        start = _ITEM_START_RE.match(line)
        if start:
            if cur:
                items.append(cur)
            cur = {"text": start.group(1)}
            continue
        if cur is None:
            continue
        field = _FIELD_RE.match(line)
        if field:
            cur[field.group(1).lower()] = field.group(2)
    if cur:
        items.append(cur)
    return items


def parse_compliance(output: str) -> dict:
    m = _BLOCK_RE.search(output or "")
    if not m:
        return {"status": "unknown", "findings": [], "content": (output or "").strip()}
    block = m.group(1).strip()
    content = _BLOCK_RE.sub("", output, count=1).strip()
    return {"status": _status_of(block), "findings": _parse_items(block), "content": content}


EXTRACTORS: dict[str, Callable[[str], dict]] = {
    "compliance_screen": parse_compliance,
}
