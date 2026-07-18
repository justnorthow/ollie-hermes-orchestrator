"""Browser-facing STT/TTS endpoints (Ollie Voice v1).

Upstream hermes-agent's API server has no audio surface (its capability
flags hardcode audio_api=false), so the orchestrator provides the HTTP
plumbing between the browser and the box's speech engines: faster-whisper
for transcription, edge-tts for synthesis. Auth mirrors src/api/prefs.py:
router-level bearer plus the trusted X-Auth-User-Id header set by nginx's
cryptographic auth_request (unforgeable by the browser).
"""
import asyncio
import io
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from src.agents_json import read_agents
from src.auth import require_bearer
from src.rate_limit import TokenBucket

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/audio", tags=["audio"], dependencies=[Depends(require_bearer)])

_MAX_AUDIO_BYTES = 15 * 1024 * 1024
_MAX_SPEAK_CHARS = 5000
_FALLBACK_VOICE = "en-US-AndrewMultilingualNeural"

# Interactive endpoints; the bucket is an abuse backstop, not a quota.
_bucket = TokenBucket(rate_per_min=30)


def _trusted_user_id(request: Request) -> str:
    user_id = request.headers.get("X-Auth-User-Id", "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="not signed in")
    return user_id


def _rate_check(user_id: str) -> None:
    if not _bucket.take(user_id):
        raise HTTPException(status_code=429, detail="rate limited")


class SpeakRequest(BaseModel):
    text: str
    agentId: str = ""


def _resolve_voice(agent_id: str, cfg) -> str:
    """Agent voice -> TTS_DEFAULT_VOICE -> hardcoded default. Never raises:
    TTS should degrade to a default voice, not gate on config problems."""
    try:
        for e in read_agents(cfg.hermes_stack_dir / ".env"):
            if e.id == agent_id:
                if e.voice:
                    return e.voice
                break
    except Exception:
        _logger.warning("voice resolution failed for %s", agent_id, exc_info=True)
    return os.environ.get("TTS_DEFAULT_VOICE", "").strip() or _FALLBACK_VOICE


async def _synthesize(text: str, voice: str) -> bytes:
    import edge_tts

    buf = bytearray()
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            buf.extend(chunk["data"])
    return bytes(buf)


@router.post("/speak")
async def speak(body: SpeakRequest, request: Request) -> Response:
    _rate_check(_trusted_user_id(request))
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty text")
    if len(text) > _MAX_SPEAK_CHARS:
        raise HTTPException(status_code=413, detail="text too long")
    voice = _resolve_voice(body.agentId, request.app.state.config)
    try:
        audio = await asyncio.wait_for(_synthesize(text, voice), timeout=60.0)
    except Exception as exc:
        _logger.warning("tts synthesis failed (voice=%s): %s", voice, exc, exc_info=True)
        raise HTTPException(status_code=502, detail="synthesis failed")
    if not audio:
        raise HTTPException(status_code=502, detail="synthesis returned no audio")
    return Response(content=audio, media_type="audio/mpeg")
