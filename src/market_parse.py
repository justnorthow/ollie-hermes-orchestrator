"""Parse an uploaded MLS export (PDF/CSV/image) into the standard market-data
draft via the agent's local Hermes gateway (OpenAI-compatible chat endpoint,
mirroring src/persona_polish.py). Pure helpers here; the route wires them up.
The model output is a DRAFT — a human confirms before anything is saved."""
from __future__ import annotations

import base64
import io
import json
import re
from datetime import date

import httpx
from pypdf import PdfReader

MAX_UPLOAD_BYTES = 10 * 1024 * 1024

_FIGURE_KEYS = ("medianSoldPrice", "inventoryMonths", "daysOnMarket", "salesVolume")
_IMAGE_EXTS = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg"}
_TEXT_EXTS = {".csv", ".txt"}

PARSE_PROMPT = (
    "You are extracting real-estate market statistics from a broker's MLS export "
    "(a market report, listings export, or dashboard screenshot).\n"
    "Return ONLY a JSON object (no prose, no code fences) with exactly these keys:\n"
    '  "label": string — the geographic area covered (neighborhood, city, county, ZIP, school district),\n'
    '  "period_label": string — the month/period covered, like "June 2026",\n'
    '  "period_end": string|null — last day of that period as YYYY-MM-DD, null if unclear,\n'
    '  "source_label": string — citation line naming the MLS/report and period, like '
    '"Unlock MLS — June 2026 Market Report",\n'
    '  "figures": {"medianSoldPrice": string, "inventoryMonths": string, '
    '"daysOnMarket": string, "salesVolume": string} — display strings like '
    '"$450,000", "3.1 months", "42 days", "87 closed sales"; use "" when the '
    "document does not state a figure,\n"
    '  "warnings": string[] — one entry per figure you could not find or are unsure about.\n'
    "NEVER invent or estimate a number that is not in the document. If the document "
    "aggregates multiple areas, use the primary/overall area and note the rest in warnings."
)


def _ext(filename: str) -> str:
    m = re.search(r"\.[A-Za-z0-9]+$", filename or "")
    return m.group(0).lower() if m else ""


def prepare_user_content(filename: str, data: bytes) -> "str | list":
    """File bytes -> model-ready content. Text (str) for pdf/csv/txt; an
    OpenAI-style content-block list for images. Raises ValueError on
    unsupported extensions or a PDF with no extractable text."""
    ext = _ext(filename)
    if ext in _TEXT_EXTS:
        return f"FILE {filename} CONTENTS:\n{data.decode('utf-8', 'replace')}"
    if ext == ".pdf":
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
        if not text:
            raise ValueError("could not extract text from PDF")
        return f"FILE {filename} CONTENTS:\n{text}"
    if ext in _IMAGE_EXTS:
        b64 = base64.b64encode(data).decode("ascii")
        return [{"type": "image_url",
                 "image_url": {"url": f"data:image/{_IMAGE_EXTS[ext]};base64,{b64}"}}]
    raise ValueError(f"unsupported file type: {ext or '(none)'}")


def call_gateway_parse(content: "str | list", gateway_port: int, gateway_key: str,
                       timeout: float = 90.0) -> str:
    """Non-streaming chat-completions call to the agent's local gateway.
    Returns the raw model text; raises RuntimeError on a non-200 response."""
    if isinstance(content, str):
        user_content: "str | list" = f"{PARSE_PROMPT}\n\n{content}"
    else:
        user_content = [{"type": "text", "text": PARSE_PROMPT}, *content]
    resp = httpx.post(
        f"http://127.0.0.1:{gateway_port}/v1/chat/completions",
        headers={"Authorization": f"Bearer {gateway_key}",
                 "Content-Type": "application/json"},
        json={"messages": [{"role": "user", "content": user_content}], "stream": False},
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"gateway returned {resp.status_code}")
    return resp.json()["choices"][0]["message"]["content"]


def _strip_fences(text: str) -> str:
    t = text.strip()
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    return t.strip()


def validate_parse_output(text: str) -> dict:
    """Model text -> validated draft dict. Coerces missing figure keys to ''
    (with a warning) rather than failing; raises ValueError only when the text
    is not a JSON object at all."""
    try:
        obj = json.loads(_strip_fences(text))
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("model did not return JSON") from exc
    if not isinstance(obj, dict):
        raise ValueError("model did not return a JSON object")

    raw_warnings = obj.get("warnings")
    raw_warnings = raw_warnings if isinstance(raw_warnings, list) else []
    warnings = [str(w) for w in raw_warnings if isinstance(w, (str, int, float))]
    raw_figures = obj.get("figures") if isinstance(obj.get("figures"), dict) else {}
    figures: dict = {}
    for k in _FIGURE_KEYS:
        v = raw_figures.get(k)
        if isinstance(v, str):
            figures[k] = v.strip()
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            figures[k] = str(v)
        else:
            figures[k] = ""
        if not figures[k] and not any(k in w for w in warnings):
            warnings.append(f"{k}: not found in the document")

    period_end = obj.get("period_end")
    if isinstance(period_end, str):
        try:
            period_end = date.fromisoformat(period_end).isoformat()
        except ValueError:
            period_end = None
    else:
        period_end = None

    def _s(key: str) -> str:
        v = obj.get(key)
        return v.strip() if isinstance(v, str) else ""

    return {"label": _s("label"), "period_label": _s("period_label"),
            "period_end": period_end, "source_label": _s("source_label"),
            "figures": figures, "warnings": warnings}
