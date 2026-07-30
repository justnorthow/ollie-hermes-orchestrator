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
    REASON_MISCONFIGURED,
    REASON_NOT_ENABLED,
    REASON_PEER_UNAVAILABLE,
    REASON_TIMEOUT,
    ConsultPost,
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


def consult_off(req: ConsultRequest, peer_port: int, gateway_key: str,
                post: ConsultPost | None = None,
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
    post: ConsultPost,
    timeout: float = 30.0,
) -> ConsultResult:
    """Ask the peer's gateway synchronously and return its reply."""
    # scripts/install.sh writes HERMES_GATEWAY_KEY= blank for the operator to
    # paste into later. Sending `Bearer ` to the peer gets a 401, which the
    # except branch below would report as peer_unavailable -- sending the
    # operator to check a gateway that is running perfectly well. Refuse before
    # the request, naming the thing that is actually wrong. Same shape as
    # src/persona_polish.py:38-39, which returns early rather than send a
    # bearer-less Authorization header.
    if not gateway_key:
        return ConsultResult.refused(
            REASON_MISCONFIGURED,
            "HERMES_GATEWAY_KEY is not set on the orchestrator — the peer was "
            "never contacted; this is orchestrator configuration, not the peer",
            peer=req.to_agent,
        )

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
        # Logged, not just returned: a refusal reaches the calling model and
        # then vanishes. This is the only record an operator has that the peer
        # was slow rather than the plugin's own client budget expiring first.
        _logger.warning("dispatch: consult %s -> %s (port %s) timed out after %ss",
                        req.from_agent, req.to_agent, peer_port, timeout)
        return ConsultResult.refused(
            REASON_TIMEOUT, f"{req.to_agent} did not answer in {timeout:g}s: {exc}",
            peer=req.to_agent,
        )
    except Exception as exc:  # noqa: BLE001 — every failure is a structured refusal
        # Same reason, and more pressing: this branch is where a rotated or
        # blank gateway key, a dead peer, and a malformed URL all land, and
        # without a log line none of them is diagnosable after the fact.
        _logger.warning("dispatch: consult %s -> %s (port %s) failed",
                        req.from_agent, req.to_agent, peer_port, exc_info=True)
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
