from src.dispatch.authority import Caps, Origin, check, resolve_origin
from src.dispatch.types import (
    REASON_CAP_EXCEEDED,
    REASON_PEER_NOT_CONSULT_ELIGIBLE,
    REASON_UNKNOWN_PEER,
    ConsultRequest,
    Teammate,
)

ROSTER = [
    Teammate("karl-m", "Karl M", "Email", "gpt-5.6-terra", "fast", True),
    Teammate("deep", "Deep", None, "gpt-5.6-sol", "heavy", False),
]
ORIGIN = Origin(user_id="u-1", tier="account_admin")


def _req(**kw):
    base = dict(from_agent="billie", session_id="sess-1", to_agent="karl-m",
                question="q", chain=())
    base.update(kw)
    return ConsultRequest(**base)


def test_origin_resolves_from_the_session_not_the_caller():
    origin = resolve_origin(
        _req(),
        owner_lookup=lambda agent, sid: "u-1" if (agent, sid) == ("billie", "sess-1") else None,
        tier_lookup=lambda inst, uid: "account_admin",
        instance_id="inst-1",
    )

    assert origin == Origin(user_id="u-1", tier="account_admin")


def test_unresolvable_session_returns_none_so_the_caller_fails_closed():
    """The single most important test in this module. A session that does not
    resolve to a human must not produce a permissive default identity."""
    origin = resolve_origin(
        _req(),
        owner_lookup=lambda agent, sid: None,
        tier_lookup=lambda inst, uid: "account_admin",
        instance_id="inst-1",
    )

    assert origin is None


def test_tier_lookup_failure_does_not_invent_an_identity():
    def boom(inst, uid):
        raise RuntimeError("supabase down")

    origin = resolve_origin(
        _req(),
        owner_lookup=lambda agent, sid: "u-1",
        tier_lookup=boom,
        instance_id="inst-1",
    )

    assert origin is None


def test_allowed_request_returns_none():
    assert check(_req(), ROSTER, ORIGIN) is None


def test_unknown_peer_is_refused():
    r = check(_req(to_agent="nobody"), ROSTER, ORIGIN)

    assert r is not None and r.ok is False
    assert r.reason == REASON_UNKNOWN_PEER


def test_heavy_peer_is_refused_with_its_own_reason():
    r = check(_req(to_agent="deep"), ROSTER, ORIGIN)

    assert r.reason == REASON_PEER_NOT_CONSULT_ELIGIBLE
    assert "deep" in r.detail or r.peer == "deep"


def test_self_consult_is_refused():
    r = check(_req(to_agent="billie"), ROSTER, ORIGIN)

    assert r is not None and r.ok is False


def test_cycle_is_refused():
    r = check(_req(chain=("john", "karl-m", "billie")), ROSTER, ORIGIN)

    assert r.reason == REASON_CAP_EXCEEDED
    assert "cycle" in r.detail.lower()


def test_hop_cap_is_refused():
    r = check(_req(chain=("a", "b", "c")), ROSTER, ORIGIN, caps=Caps(hop_cap=3))

    assert r.reason == REASON_CAP_EXCEEDED
    assert "hop" in r.detail.lower()


def test_hop_cap_boundary_allows_exactly_the_cap():
    assert check(_req(chain=("a", "b")), ROSTER, ORIGIN, caps=Caps(hop_cap=3)) is None


def test_empty_question_is_refused():
    r = check(_req(question="   "), ROSTER, ORIGIN)

    assert r is not None and r.ok is False
