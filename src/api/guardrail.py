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

_ATT_RX = re.compile(r"<!--JNOW-COMPLIANCE-ATTESTATION\s*(\{.*?\})\s*-->", re.S)
def parse_attestation(output: str):
    if not output: return None
    m = _ATT_RX.search(output)
    if not m: return None
    try: return json.loads(m.group(1))
    except Exception: return None
def strip_attestation(output: str) -> str:
    return _ATT_RX.sub("", output or "").rstrip() if output else output
def decide_attestation(att, enforce: bool) -> dict:
    """{action: deliver|withhold, event_type, screened}."""
    screened = (att or {}).get("screened")
    if screened == "pass": return {"action":"deliver","event_type":"attestation.pass","screened":"pass"}
    if screened == "na":   return {"action":"deliver","event_type":"attestation.na","screened":"na"}
    # missing attestation or screened in (fail, other)
    if enforce: return {"action":"withhold","event_type":"attestation.withheld","screened":screened}
    return {"action":"deliver","event_type":"attestation.unattested","screened":screened}
