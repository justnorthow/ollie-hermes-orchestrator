"""Shared vocabulary for agent-to-agent dispatch.

`ConsultResult` is deliberately shaped so a refusal cannot carry an answer:
`refused()` never sets `answer`. The failure mode this guards against is an
agent narrating a plausible reply it never received, which is indistinguishable
from a real answer to the human reading the transcript.
"""
from dataclasses import dataclass
from typing import Protocol

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
#: The orchestrator itself is not configured to dispatch (e.g. a blank
#: HERMES_GATEWAY_KEY, or app config unavailable). Deliberately distinct from
#: peer_unavailable: that one sends the operator to check the peer's gateway,
#: which is running fine.
REASON_MISCONFIGURED = "misconfigured"


class ConsultPost(Protocol):
    """The peer-gateway POST seam injected into consult_direct.

    Distinct from AuditPost by arity: this one takes a per-call `timeout` and
    returns the decoded body. The two seams are structurally incompatible on
    purpose -- swapping them used to fail only at runtime, inside audit.py's
    best-effort `except`, i.e. as a silently dropped audit row.
    """

    def __call__(self, url: str, headers: dict, json: dict,
                 timeout: float) -> dict: ...


class AuditPost(Protocol):
    """The governance_events POST seam injected into record_consult.

    No timeout parameter (the audit sink owns its own) and no return value.
    """

    def __call__(self, url: str, headers: dict, json: dict) -> None: ...


@dataclass(frozen=True)
class Teammate:
    agent_id: str
    display_name: str
    subtitle: str | None
    model: str | None
    speed_class: str | None
    consult_eligible: bool
    #: The two fields src/api/authz.py's can_reach() decides human->agent access
    #: on, carried through from AgentEntry so the same rule can gate dispatch
    #: without src/dispatch/ importing src/api/. Defaults are the fail-closed
    #: pair: a company-scope, manager-invisible agent is reachable only by
    #: account_admin and above, so an entry missing these fields is hidden from
    #: everyone below that tier rather than exposed to everyone.
    scope: str = "company"
    manager_visible: bool = False


@dataclass(frozen=True)
class ConsultRequest:
    from_agent: str
    session_id: str
    to_agent: str
    question: str
    #: Agent ids already in this chain, for the audit trail and for the cycle /
    #: hop tests in authority.check().
    #:
    #: DIVERGENCE FROM THE SPEC, DELIBERATE. The design doc (§2) shapes this as
    #: ["John", "billie", "karl-m"] -- the *human* at index 0, agents after. The
    #: code here treats EVERY element as an agent id (the cycle test is
    #: `to_agent in chain`, and `len(chain)` is counted as a hop count). Adopting
    #: the spec's shape later without changing authority.check() would silently
    #: turn a hop cap of 3 into an effective cap of 2 and would let a chain
    #: "cycle" on an agent whose id happened to match the human's name. If the
    #: spec's shape is adopted, change authority.check() in the same commit.
    #:
    #: Note also that chain is caller-asserted and therefore NOT a security
    #: control: the plugin cannot be made to propagate it honestly, so recursion
    #: is bounded server-side by src/dispatch/inflight.py instead. This field is
    #: retained for the audit trail and is length-bounded at the API boundary
    #: (src/api/dispatch.py) before it is written to governance_events.
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
