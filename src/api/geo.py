"""Geography key normalization (SP3). DUPLICATE of jnow-workspace
development/core/market-data/normalize.py — keep the rule + test table in sync.
The ingest job writes region_key with this rule; the lookup must match it."""

import re


def normalize_region_key(region_type: str, value: str) -> str:
    v = (value or "").strip().lower()
    v = re.sub(r"\s+", " ", v)
    v = re.sub(r",?\s*tx$", "", v).strip()
    if region_type == "county":
        v = re.sub(r"\s+county$", "", v).strip()
    elif region_type == "zip":
        digits = re.sub(r"\D", "", value or "")
        v = digits[:5]
    return v
