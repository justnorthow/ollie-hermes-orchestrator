"""Provenance resolution and caps — the security core of dispatch.

Two properties this module exists to hold:

1. **Provenance is resolved, not asserted.** The calling agent supplies only its
   own agent id and the session id Hermes gave it. The human is derived from
   `agent_sessions` via the injected `owner_lookup`. An agent claiming "John asked
   for this" is model output and is never accepted as identity.

2. **Fail closed.** Any failure to resolve — unknown session, lookup error,
   tier lookup raising — yields `None`, and the caller must refuse. There is
   deliberately no permissive default: a bug here would let an unattributed
   request run with someone's authority.
"""
import logging
from dataclasses import dataclass

from src.dispatch.types import (
    REASON_CAP_EXCEEDED,
    REASON_FORBIDDEN,
    REASON_PEER_NOT_CONSULT_ELIGIBLE,
    REASON_UNKNOWN_PEER,
    ConsultRequest,
    ConsultResult,
    Teammate,
)

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Caps:
    hop_cap: int = 3
    fan_out_cap: int = 5


@dataclass(frozen=True)
class Origin:
    user_id: str
    tier: str


def resolve_origin(
    req: ConsultRequest,
    owner_lookup,
    tier_lookup,
    instance_id: str,
) -> Origin | None:
    """Derive the originating human from the session. None means refuse."""
    try:
        user_id = owner_lookup(req.from_agent, req.session_id)
    except Exception:
        _logger.warning("dispatch owner_lookup failed", exc_info=True)
        return None
    if not user_id:
        return None
    try:
        tier = tier_lookup(instance_id, user_id)
    except Exception:
        _logger.warning("dispatch tier_lookup failed", exc_info=True)
        return None
    if not tier:
        return None
    return Origin(user_id=user_id, tier=tier)


def check(
    req: ConsultRequest,
    roster: list[Teammate],
    origin: Origin,
    caps: Caps = Caps(),
) -> ConsultResult | None:
    """Return a refusal, or None when the request is allowed."""
    if not req.question.strip():
        return ConsultResult.refused(
            REASON_FORBIDDEN, "question is empty", peer=req.to_agent
        )

    if req.to_agent == req.from_agent:
        return ConsultResult.refused(
            REASON_FORBIDDEN, "an agent cannot consult itself", peer=req.to_agent
        )

    # Cycle before hop cap: a loop is a more specific diagnosis than "too deep".
    if req.to_agent in req.chain:
        return ConsultResult.refused(
            REASON_CAP_EXCEEDED,
            f"cycle — {req.to_agent} is already in this chain",
            peer=req.to_agent,
        )

    if len(req.chain) >= caps.hop_cap:
        return ConsultResult.refused(
            REASON_CAP_EXCEEDED,
            f"hop cap of {caps.hop_cap} reached",
            peer=req.to_agent,
        )

    peer = next((t for t in roster if t.agent_id == req.to_agent), None)
    if peer is None:
        return ConsultResult.refused(
            REASON_UNKNOWN_PEER,
            f"{req.to_agent} is not on this box's roster",
            peer=req.to_agent,
        )

    if not peer.consult_eligible:
        return ConsultResult.refused(
            REASON_PEER_NOT_CONSULT_ELIGIBLE,
            f"{peer.agent_id} runs {peer.model or 'an unknown model'} "
            f"(speed_class={peer.speed_class or 'unknown'}) and cannot be consulted "
            f"inline — name it to your human instead",
            peer=peer.agent_id,
        )

    return None
