"""Deterministic, non-LLM TRAIGA guardrails for the run-proxy."""
from __future__ import annotations
import json, re
from pathlib import Path
_HERE = Path(__file__).resolve().parent
_PROHIB = None
def load_prohibitions(path=None):
    p = Path(path) if path else _HERE / "traiga_prohibitions.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    for d in data:
        d["_rx"] = [re.compile(pat, re.I) for pat in d.get("patterns", [])]
    return data
def screen_input(text, prohibitions) -> dict:
    """{decision: allow|flag|block, prohibition, citation, matched}. block wins over flag."""
    s = text or ""
    if not isinstance(s, str) or not s.strip():
        return {"decision": "allow", "prohibition": None, "citation": None, "matched": None}
    flagged = None
    for d in prohibitions:
        for rx in d["_rx"]:
            if rx.search(s):
                if d["action"] == "block":
                    return {"decision": "block", "prohibition": d["id"], "citation": d["citation"], "matched": rx.pattern}
                flagged = flagged or {"decision": "flag", "prohibition": d["id"], "citation": d["citation"], "matched": rx.pattern}
    return flagged or {"decision": "allow", "prohibition": None, "citation": None, "matched": None}
