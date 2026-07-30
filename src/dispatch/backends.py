"""Mode drivers. `direct` owns the only peer-gateway call in the codebase.

Every failure becomes a structured ConsultResult. Nothing here raises to the
caller, because the caller is ultimately a language model: an exception surfaces
to it as an empty tool result, which is exactly the condition under which a model
invents a plausible answer.
"""
import logging

from src.dispatch.types import (
    MODE_DIRECT,
    MODE_OFF,
    REASON_NOT_ENABLED,
    REASON_PEER_UNAVAILABLE,
    REASON_TIMEOUT,
    ConsultRequest,
    ConsultResult,
)

_logger = logging.getLogger(__name__)

_PROMPT = (
    "You are being consulted by a teammate agent, {frm}, on behalf of its human. "
    "Answer directly and concisely from your own expertise. If the question is "
    "outside what you know, say so plainly rather than guessing.\n\n"
    "Question from {frm}:\n{question}"
)


def consult_off(req: ConsultRequest, peer_port: int, gateway_key: str, post=None,
                timeout: float = 30.0) -> ConsultResult:
    """Refuse without touching the network. Never calls `post`.

    Accepts the same `(req, peer_port, gateway_key, post, timeout=...)` shape
    consult_direct does, even though it ignores all of it, so it stays
    interchangeable through `backend_for`. dispatch.py's own MODE_OFF early
    return means this is never reached in production today -- but if that
    guard is ever removed, `backend_for(MODE_OFF)` must still be callable with
    the same keyword shape every other driver is called with (dispatch.py:197
    passes `timeout=` explicitly), or the fallback is a TypeError/500 instead
    of the graceful refusal this function exists to give.
    """
    return ConsultResult.refused(
        REASON_NOT_ENABLED,
        "dispatch is disabled on this instance (DISPATCH_MODE=off)",
        peer=req.to_agent,
    )


def consult_direct(
    req: ConsultRequest,
    peer_port: int,
    gateway_key: str,
    post,
    timeout: float = 30.0,
) -> ConsultResult:
    """Ask the peer's gateway synchronously and return its reply."""
    url = f"http://127.0.0.1:{peer_port}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {gateway_key}",
        "Content-Type": "application/json",
    }
    body = {
        "messages": [
            {
                "role": "user",
                "content": _PROMPT.format(frm=req.from_agent, question=req.question),
            }
        ],
        "stream": False,
    }

    try:
        data = post(url, headers, body, timeout)
    except TimeoutError as exc:
        return ConsultResult.refused(
            REASON_TIMEOUT, f"{req.to_agent} did not answer in {timeout:g}s: {exc}",
            peer=req.to_agent,
        )
    except Exception as exc:  # noqa: BLE001 — every failure is a structured refusal
        return ConsultResult.refused(
            REASON_PEER_UNAVAILABLE, f"{exc}", peer=req.to_agent
        )

    try:
        answer = data["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001 — unexpected shape is a refusal, not a crash
        return ConsultResult.refused(
            REASON_PEER_UNAVAILABLE,
            "peer returned an unrecognised response shape",
            peer=req.to_agent,
        )

    if not isinstance(answer, str) or not answer.strip():
        return ConsultResult.refused(
            REASON_PEER_UNAVAILABLE, "peer returned an empty answer",
            peer=req.to_agent,
        )

    return ConsultResult.granted(answer, peer=req.to_agent)


_BACKENDS = {MODE_OFF: consult_off, MODE_DIRECT: consult_direct}


def backend_for(mode: str):
    """Driver for `mode`. `local` and `linear` are not implemented in this slice."""
    try:
        return _BACKENDS[mode]
    except KeyError:
        raise ValueError(f"dispatch mode not available in this build: {mode!r}") from None
