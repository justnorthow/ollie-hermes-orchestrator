import json
import logging
import os
import urllib.parse
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from src.api.extractors import EXTRACTORS
from src.auth import require_bearer
from .guardrail import screen_input, load_prohibitions

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["runs"], dependencies=[Depends(require_bearer)])

PROHIBITIONS = load_prohibitions()


def _gateway_base(agent: str) -> str | None:
    """Per-agent Hermes gateway base URL (the run endpoints live at {base}/v1/runs).
    Each agent has its OWN gateway on a distinct host:port (e.g. real-estate ->
    http://127.0.0.1:8644), so resolve per-agent — do NOT append the agent to a
    shared base. HERMES_GATEWAY_URLS is a JSON map {agent: url}; HERMES_GATEWAY_URL
    is a single-agent fallback."""
    raw = os.environ.get("HERMES_GATEWAY_URLS", "").strip()
    if raw:
        try:
            url = json.loads(raw).get(agent)
        except (ValueError, AttributeError):
            _logger.warning("HERMES_GATEWAY_URLS is not a valid JSON object")
            url = None
        if url:
            return str(url).rstrip("/")
    base = os.environ.get("HERMES_GATEWAY_URL", "").strip().rstrip("/")
    return base or None


def _gateway_headers() -> dict:
    h = {"content-type": "application/json"}
    key = os.environ.get("HERMES_GATEWAY_KEY", "").strip()
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def _create_run(agent: str, body: bytes) -> tuple[int, bytes]:
    base = _gateway_base(agent)
    resp = httpx.post(f"{base}/v1/runs", content=body, headers=_gateway_headers(), timeout=30.0)
    return resp.status_code, resp.content


async def _stream_upstream(base: str, run_id: str) -> AsyncIterator[bytes]:
    headers = {**_gateway_headers(), "accept": "text/event-stream"}
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("GET", f"{base}/v1/runs/{run_id}/events", headers=headers) as resp:
            async for chunk in resp.aiter_bytes():
                yield chunk


def _extract_output(buffer: str) -> str | None:
    out = None
    for frame in buffer.split("\n\n"):
        for line in frame.split("\n"):
            line = line.strip()
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except Exception:
                continue
            if (ev.get("event") or ev.get("type")) == "run.completed" and isinstance(ev.get("output"), str):
                out = ev["output"]
    return out


def _write_event(row: dict, url: str, key: str) -> None:
    resp = httpx.post(
        f"{url}/rest/v1/governance_events",
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json", "Prefer": "return=minimal"},
        json=row, timeout=10.0,
    )
    resp.raise_for_status()


def _extract_input(body: bytes) -> str:
    """Extract the user prompt from a run-create body. Any error -> '' (allows, never raises)."""
    try:
        data = json.loads(body)
        val = data.get("input", "")
        return val if isinstance(val, str) else ""
    except Exception:
        return ""


def _emit_guardrail(request: Request, agent: str, event_type: str, verdict: dict, snippet: str = "") -> None:
    """Best-effort: write a guardrail governance event. Never raises."""
    try:
        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not (url and key):
            return
        email = request.headers.get("X-Auth-Email", "").strip()
        role = request.headers.get("X-Auth-Role", "").strip() or "agent"
        # Store a SHORT redacted snippet — never the full prohibited text.
        safe_content = (snippet[:80] + "…") if len(snippet) > 80 else snippet or "[redacted]"
        _write_event({
            "user_email": email,
            "user_role": role,
            "app": agent,
            "event_type": event_type,
            "status": verdict.get("decision"),
            "title": verdict.get("citation"),
            "findings": verdict.get("prohibition"),
            "content": safe_content,
            "run_id": None,
        }, url, key)
    except Exception:
        _logger.warning("_emit_guardrail failed", exc_info=True)


@router.post("/v1/runs/{agent}")
async def create_run(agent: str, request: Request):
    if not _gateway_base(agent):
        return JSONResponse({"detail": "Run proxy not configured"}, status_code=503)
    body = await request.body()
    inp = _extract_input(body)
    v = screen_input(inp, PROHIBITIONS)
    if v["decision"] == "block":
        _emit_guardrail(request, agent, "guardrail.blocked", v, inp[:60])
        return JSONResponse(
            {"detail": "This request was blocked by TRAIGA policy.", "citation": v["citation"]},
            status_code=403,
        )
    if v["decision"] == "flag":
        _emit_guardrail(request, agent, "guardrail.flagged", v, inp[:60])
    status, content = _create_run(agent, body)
    return Response(content=content, status_code=status, media_type="application/json")


@router.get("/v1/runs/{agent}/{run_id}/events")
async def run_events(agent: str, run_id: str, request: Request):
    base = _gateway_base(agent)
    if not base:
        return JSONResponse({"detail": "Run proxy not configured"}, status_code=503)

    email = request.headers.get("X-Auth-Email", "").strip()
    role = request.headers.get("X-Auth-Role", "").strip() or "agent"
    gov_app = request.headers.get("X-Gov-App", "").strip()
    gov_event = request.headers.get("X-Gov-Event-Type", "").strip()
    gov_title = urllib.parse.unquote(request.headers.get("X-Gov-Title", "").strip())
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()

    async def gen():
        chunks: list[bytes] = []
        async for chunk in _stream_upstream(base, run_id):
            chunks.append(chunk)
            yield chunk
        # Upstream closed. Capture only a governed run; never let it break the stream.
        extractor = EXTRACTORS.get(gov_event)
        if not (gov_app and extractor and email and url and key):
            return
        try:
            output = _extract_output(b"".join(chunks).decode("utf-8", "replace"))
            if output is None:
                return
            parsed = extractor(output)
            _write_event({
                "user_email": email, "user_role": role,
                "app": gov_app, "event_type": gov_event,
                "status": parsed["status"], "title": gov_title or None,
                "findings": parsed["findings"], "content": parsed["content"],
                "run_id": run_id,
            }, url, key)
        except Exception:
            _logger.exception("governance capture failed for run %s", run_id)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
