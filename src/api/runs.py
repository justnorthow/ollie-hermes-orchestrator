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
from .guardrail import screen_input, load_prohibitions, parse_attestation, strip_attestation, decide_attestation

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["runs"], dependencies=[Depends(require_bearer)])

PROHIBITIONS = load_prohibitions()

from src.api import sessions as _sessions_store

# run_id -> creating user's Supabase UUID. In-memory (restart loses it; the
# events-side check then falls through to allow — acceptable v1: run ids are
# unguessable and expire quickly).
_RUN_OWNERS: dict[str, str] = {}
_RUN_OWNERS_MAX = 5000

# Logged once per process (see _warn_identity_header_skew) to flag stale nginx
# or an old validator that forwards X-Auth-Email without X-Auth-User-Id,
# which silently skips the Phase 1 ownership gate below.
_IDENTITY_SKEW_WARNED = False


def _warn_identity_header_skew(request: Request) -> None:
    global _IDENTITY_SKEW_WARNED
    if _IDENTITY_SKEW_WARNED:
        return
    email = request.headers.get("X-Auth-Email", "").strip()
    user_id = request.headers.get("X-Auth-User-Id", "").strip()
    if email and not user_id:
        _IDENTITY_SKEW_WARNED = True
        _logger.warning(
            "identity header skew: X-Auth-Email present without X-Auth-User-Id — stale nginx or old validator?"
        )


def _session_owner(agent: str, session_id: str) -> str | None:
    return _sessions_store.get_session_owner(agent, session_id)


def _record_session(agent: str, session_id: str, user_id: str) -> None:
    _sessions_store.record_session(agent, session_id, user_id)


def _touch_session(agent: str, session_id: str) -> None:
    _sessions_store.touch_session(agent, session_id)


def _remember_run_owner(run_id: str, user_id: str) -> None:
    if len(_RUN_OWNERS) >= _RUN_OWNERS_MAX:
        _RUN_OWNERS.pop(next(iter(_RUN_OWNERS)))
    _RUN_OWNERS[run_id] = user_id


def _extract_session_id_from_body(body: bytes) -> str | None:
    try:
        val = json.loads(body).get("session_id")
        return val if isinstance(val, str) and val else None
    except Exception:
        return None


def _scan_frames_for_session(raw: str) -> str | None:
    """Find a session_id in run.completed (or any) SSE data frames."""
    sid = None
    for frame in raw.split("\n\n"):
        sid = _scan_frame_for_session(frame) or sid
    return sid


def _scan_frame_for_session(frame: str) -> str | None:
    """Find a session_id in a SINGLE complete SSE frame's data line(s)."""
    for line in frame.split("\n"):
        line = line.strip()
        if not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:].strip())
        except Exception:
            continue
        if isinstance(ev.get("session_id"), str) and ev["session_id"]:
            return ev["session_id"]
    return None


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


def _gateway_post(agent: str, path: str, body: bytes = b"") -> tuple[int, bytes]:
    base = _gateway_base(agent)
    resp = httpx.post(f"{base}{path}", content=body, headers=_gateway_headers(), timeout=30.0)
    return resp.status_code, resp.content


def _gateway_get(agent: str, path: str) -> tuple[int, bytes]:
    base = _gateway_base(agent)
    resp = httpx.get(f"{base}{path}", headers=_gateway_headers(), timeout=30.0)
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

    _warn_identity_header_skew(request)

    # Phase 1 session-ownership gate (fail-closed). Identity-less callers hold
    # the orchestrator bearer key and are inside the trust boundary.
    user_id = request.headers.get("X-Auth-User-Id", "").strip()
    session_id = _extract_session_id_from_body(body)
    owned_continue = False
    if user_id and session_id:
        if _session_owner(agent, session_id) != user_id:
            return JSONResponse({"detail": "Session not found"}, status_code=403)
        owned_continue = True

    status, content = _create_run(agent, body)
    if owned_continue and status < 300:
        try:
            _touch_session(agent, session_id)
        except Exception:
            pass
    if user_id and status < 300:
        try:
            run_id = json.loads(content).get("run_id")
            if isinstance(run_id, str) and run_id:
                _remember_run_owner(run_id, user_id)
        except Exception:
            pass
    return Response(content=content, status_code=status, media_type="application/json")


def _run_owner_gate(request: Request, run_id: str) -> JSONResponse | None:
    user_id = request.headers.get("X-Auth-User-Id", "").strip()
    owner = _RUN_OWNERS.get(run_id)
    if owner and user_id and owner != user_id:
        return JSONResponse({"detail": "Run not found"}, status_code=403)
    return None


@router.post("/v1/runs/{agent}/{run_id}/stop")
async def stop_run(agent: str, run_id: str, request: Request):
    if not _gateway_base(agent):
        return JSONResponse({"detail": "Run proxy not configured"}, status_code=503)
    denied = _run_owner_gate(request, run_id)
    if denied:
        return denied
    status, content = _gateway_post(agent, f"/v1/runs/{run_id}/stop")
    return Response(content=content, status_code=status, media_type="application/json")


@router.post("/v1/runs/{agent}/{run_id}/approval")
async def approve_run(agent: str, run_id: str, request: Request):
    if not _gateway_base(agent):
        return JSONResponse({"detail": "Run proxy not configured"}, status_code=503)
    denied = _run_owner_gate(request, run_id)
    if denied:
        return denied
    body = await request.body()
    status, content = _gateway_post(agent, f"/v1/runs/{run_id}/approval", body)
    return Response(content=content, status_code=status, media_type="application/json")


@router.get("/v1/runs/{agent}")
async def list_runs(agent: str, request: Request):
    if not _gateway_base(agent):
        return JSONResponse({"detail": "Run proxy not configured"}, status_code=503)
    qs = request.url.query
    path = f"/v1/runs?{qs}" if qs else "/v1/runs"
    status, content = _gateway_get(agent, path)
    return Response(content=content, status_code=status, media_type="application/json")


@router.get("/v1/runs/{agent}/{run_id}/events")
async def run_events(agent: str, run_id: str, request: Request):
    base = _gateway_base(agent)
    if not base:
        return JSONResponse({"detail": "Run proxy not configured"}, status_code=503)

    denied = _run_owner_gate(request, run_id)
    if denied:
        return denied
    user_id = request.headers.get("X-Auth-User-Id", "").strip()
    run_owner = _RUN_OWNERS.get(run_id)

    email = request.headers.get("X-Auth-Email", "").strip()
    role = request.headers.get("X-Auth-Role", "").strip() or "agent"
    gov_app = request.headers.get("X-Gov-App", "").strip()
    gov_event = request.headers.get("X-Gov-Event-Type", "").strip()
    gov_title = urllib.parse.unquote(request.headers.get("X-Gov-Title", "").strip())
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()

    async def gen():
        chunks: list[bytes] = []
        extractor = EXTRACTORS.get(gov_event)

        if gov_app:
            # GOVERNED PATH (C v1.1 — unified): buffer the output, then apply the attestation
            # gate to EVERY governed run for enforcement (deliver/withhold + attestation event).
            # Where the event-type has a registered extractor, ALSO write the rich capture row —
            # so a run like the newsletter's `compliance_screen` gets both enforcement and its
            # detailed findings. Non-governed runs (no X-Gov-App) stream unchanged in the ELSE.
            async for chunk in _stream_upstream(base, run_id):
                chunks.append(chunk)
            try:
                raw = b"".join(chunks).decode("utf-8", "replace")
                cap_user = run_owner or user_id
                if cap_user:
                    sid = _scan_frames_for_session(raw)
                    if sid:
                        _record_session(agent, sid, cap_user)
                out = _extract_output(raw)
                if out is None:
                    # No run.completed frame found; deliver original bytes unchanged.
                    for chunk in chunks:
                        yield chunk
                    return
                att = parse_attestation(out)
                enforce = gov_app in {
                    a for a in os.environ.get("GUARDRAIL_ENFORCE_APPS", "").split(",") if a
                }
                d = decide_attestation(att, enforce)
                if url and key:
                    # Enforcement record (attestation gate) — always, for every governed run.
                    _write_event({
                        "user_email": email, "user_role": role,
                        "app": gov_app, "event_type": d["event_type"],
                        "status": d["action"],
                        "title": None,
                        # findings is jsonb NOT NULL default '[]'; an explicit null violates
                        # the constraint (the default only applies when omitted), so coalesce.
                        "findings": (att or {}).get("rules") or [],
                        "content": None,
                        "run_id": run_id,
                    }, url, key)
                    # Rich capture (additive) — only when the event-type has a registered
                    # extractor. Never let a capture failure abort delivery.
                    if extractor and email:
                        try:
                            parsed = extractor(strip_attestation(out))
                            _write_event({
                                "user_email": email, "user_role": role,
                                "app": gov_app, "event_type": gov_event,
                                "status": parsed["status"], "title": gov_title or None,
                                "findings": parsed["findings"], "content": parsed["content"],
                                "run_id": run_id,
                            }, url, key)
                        except Exception:
                            _logger.exception("governance capture failed for run %s", run_id)
                if d["action"] == "withhold":
                    frame_out = "Held for compliance review."
                else:
                    frame_out = strip_attestation(out)
                yield (
                    "data: " + json.dumps({"event": "run.completed", "output": frame_out}) + "\n\n"
                ).encode()
            except Exception:
                _logger.exception("attestation gate failed for run %s", run_id)
                # Fallback: deliver original buffered bytes unchanged; never 500 or lose output.
                for chunk in chunks:
                    yield chunk
        else:
            # NON-GOVERNED PATH: incrementally parse each chunk for a
            # session_id BEFORE yielding it, then yield the chunk unchanged.
            # This fixes two bugs the old rolling-64KB-tail approach had:
            #   (a) a run.completed data line bigger than 64KB used to get
            #       truncated, so JSON parsing failed and no ownership row was
            #       ever written (the creator's next message in that thread
            #       then 403'd with no recovery but the backfill script);
            #   (b) capture used to happen only in code positioned AFTER the
            #       loop, so a client disconnect (which cancels this generator
            #       mid-loop) could strand the session before that code ran.
            # Parsing must complete BEFORE `yield chunk` (not after): a
            # generator pauses exactly at yield, so code placed after the
            # yield for chunk N only resumes when the consumer asks for
            # chunk N+1 -- which never happens on a disconnect right after
            # chunk N. Parsing first means capture has already happened by
            # the time this generator yields (and could be cancelled).
            #
            # The buffer holds only the current INCOMPLETE frame (never the
            # whole run), so there is no size cap. A "\n\n" frame boundary
            # can itself be split across two chunks; accumulating into
            # `pending` and re-splitting the COMBINED bytes on every chunk
            # (rather than splitting each chunk in isolation) handles that
            # correctly -- everything before the last "\n\n" is one or more
            # complete frames, and the remainder (no trailing "\n\n" yet)
            # stays buffered as the new incomplete-frame tail.
            cap_user = run_owner or user_id
            recorded = False
            pending = b""
            async for chunk in _stream_upstream(base, run_id):
                if not recorded and cap_user:
                    try:
                        pending += chunk
                        if b"\n\n" in pending:
                            *complete, pending = pending.split(b"\n\n")
                            for frame_bytes in complete:
                                sid = _scan_frame_for_session(frame_bytes.decode("utf-8", "replace"))
                                if sid:
                                    _record_session(agent, sid, cap_user)
                                    recorded = True
                                    break
                    except Exception:
                        # Parsing must never affect delivery -- chunk is
                        # yielded unconditionally below regardless.
                        _logger.warning(
                            "incremental session-id parse failed for run %s", run_id, exc_info=True
                        )
                yield chunk

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
