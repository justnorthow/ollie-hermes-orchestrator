"""Shared vocabulary for agent-to-agent dispatch.

`ConsultResult` is deliberately shaped so a refusal cannot carry an answer:
`refused()` never sets `answer`. The failure mode this guards against is an
agent narrating a plausible reply it never received, which is indistinguishable
from a real answer to the human reading the transcript.
"""
from dataclasses import dataclass

MODE_OFF = "off"
MODE_DIRECT = "direct"
MODE_LOCAL = "local"
MODE_LINEAR = "linear"
VALID_MODES = frozenset({MODE_OFF, MODE_DIRECT, MODE_LOCAL, MODE_LINEAR})

REASON_NOT_ENABLED = "not_enabled"
REASON_FORBIDDEN = "forbidden"
REASON_UNKNOWN_PEER = "unknown_peer"
REASON_PEER_NOT_CONSULT_ELIGIBLE = "peer_not_consult_eligible"
REASON_CAP_EXCEEDED = "cap_exceeded"
REASON_TIMEOUT = "timeout"
REASON_PEER_UNAVAILABLE = "peer_unavailable"


@dataclass(frozen=True)
class Teammate:
    agent_id: str
    display_name: str
    subtitle: str | None
    model: str | None
    speed_class: str | None
    consult_eligible: bool


@dataclass(frozen=True)
class ConsultRequest:
    from_agent: str
    session_id: str
    to_agent: str
    question: str
    #: agent ids already in this chain, for cycle detection and the hop cap
    chain: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConsultResult:
    ok: bool
    answer: str | None = None
    reason: str | None = None
    detail: str = ""
    peer: str | None = None

    @classmethod
    def granted(cls, answer: str, peer: str | None = None) -> "ConsultResult":
        return cls(ok=True, answer=answer, peer=peer)

    @classmethod
    def refused(
        cls, reason: str, detail: str = "", peer: str | None = None
    ) -> "ConsultResult":
        return cls(ok=False, answer=None, reason=reason, detail=detail, peer=peer)
