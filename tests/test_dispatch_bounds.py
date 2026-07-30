"""API-level bounds on caller-controlled data, and the in-flight refusal.

`chain` and `question` arrive from an agent process and land in an append-only
audit table (src/dispatch/audit.py) that nobody can redact afterwards.
"""
import pytest
from fastapi.testclient import TestClient

from src.api.dispatch import _MAX_CHAIN_LINK_CHARS, _MAX_CHAIN_LINKS, _clamp_chain

AUTH = {"Authorization": "Bearer topsecret"}


@pytest.fixture
def client(fake_env):
    from src.api.main import create_app

    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    from src.dispatch.types import Teammate

    monkeypatch.setenv("DISPATCH_MODE", "direct")
    monkeypatch.setattr("src.api.dispatch.get_session_owner", lambda a, s: "u-1")
    monkeypatch.setattr("src.api.dispatch.resolve_tier", lambda i, u: "account_admin")
    monkeypatch.setattr(
        "src.api.dispatch.build_roster",
        lambda *a, **kw: [Teammate("karl-m", "K", None, "gpt-5.6-terra", "fast",
                                   True, scope="user")],
    )
    monkeypatch.setattr("src.api.dispatch.port_for", lambda agent, entries: 8643)


def _consult(client, **overrides):
    body = {"from_agent": "billie", "session_id": "s1", "to_agent": "karl-m",
            "question": "q"}
    body.update(overrides)
    return client.post("/v1/dispatch/consult", headers=AUTH, json=body).json()


# --- chain bounds ------------------------------------------------------------

def test_chain_length_is_clamped():
    assert len(_clamp_chain(["x"] * 500)) == _MAX_CHAIN_LINKS


def test_each_chain_link_is_clamped():
    clamped = _clamp_chain(["y" * 10_000])

    assert len(clamped[0]) == _MAX_CHAIN_LINK_CHARS


def test_a_normal_chain_passes_through_unchanged():
    assert _clamp_chain(["john", "billie"]) == ("john", "billie")


def test_an_oversized_chain_reaches_the_audit_row_bounded(client, monkeypatch):
    """The clamp has to happen before record_consult, not inside it: the audit
    write is best-effort and swallows its own exceptions, so an unbounded value
    would be discovered as a silently missing row, if at all."""
    recorded = []
    monkeypatch.setattr("src.api.dispatch.record_consult",
                        lambda req, *a, **kw: recorded.append(req))
    monkeypatch.setattr("src.api.dispatch.backend_for", lambda mode: _granting)

    _consult(client, chain=["z" * 5_000] * 200)

    assert len(recorded[0].chain) == _MAX_CHAIN_LINKS
    assert all(len(link) == _MAX_CHAIN_LINK_CHARS for link in recorded[0].chain)


# --- question bound ----------------------------------------------------------

def test_an_oversized_question_is_refused(client, monkeypatch):
    from src.dispatch.authority import Caps

    monkeypatch.setattr("src.api.dispatch.record_consult", lambda *a, **kw: None)
    called = []

    def driver(req, port, key, post, **kw):
        called.append(req)
        raise AssertionError("an oversized question must not reach a peer")

    monkeypatch.setattr("src.api.dispatch.backend_for", lambda mode: driver)

    body = _consult(client, question="q" * (Caps().question_cap + 1))

    assert body["ok"] is False
    assert body["reason"] == "forbidden"
    assert called == []


def test_a_question_at_the_cap_is_allowed(client, monkeypatch):
    from src.dispatch.authority import Caps

    monkeypatch.setattr("src.api.dispatch.record_consult", lambda *a, **kw: None)
    monkeypatch.setattr("src.api.dispatch.backend_for", lambda mode: _granting)

    assert _consult(client, question="q" * Caps().question_cap)["ok"] is True


# --- the in-flight refusal, end to end ---------------------------------------

def _granting(req, port, key, post, **kw):
    from src.dispatch.types import ConsultResult

    return ConsultResult.granted("sure", peer=req.to_agent)


def test_a_re_entrant_consult_is_refused_as_cap_exceeded(client, monkeypatch):
    """The peer's own model turn asking back re-enters this endpoint while the
    first consult is still open. Without the in-flight hold nothing refuses it:
    ids differ, and chain is empty on every real call."""
    monkeypatch.setattr("src.api.dispatch.record_consult", lambda *a, **kw: None)
    seen = []

    def reentrant_driver(req, port, key, post, **kw):
        from src.dispatch.types import ConsultResult

        # karl-m's turn calls back for the same human and the same peer.
        seen.append(_consult(client))
        return ConsultResult.granted("sure", peer=req.to_agent)

    monkeypatch.setattr("src.api.dispatch.backend_for", lambda mode: reentrant_driver)

    outer = _consult(client)

    assert outer["ok"] is True
    assert seen[0]["ok"] is False
    assert seen[0]["reason"] == "cap_exceeded"
    assert seen[0]["answer"] is None


def test_the_hold_is_released_after_a_completed_consult(client, monkeypatch):
    """Two consults in a row must both succeed. A hold leaked on the happy path
    would block this (human, peer) pair until the process restarted."""
    monkeypatch.setattr("src.api.dispatch.record_consult", lambda *a, **kw: None)
    monkeypatch.setattr("src.api.dispatch.backend_for", lambda mode: _granting)

    assert _consult(client)["ok"] is True
    assert _consult(client)["ok"] is True


def test_the_hold_is_released_when_the_driver_raises(client, monkeypatch):
    """Drivers are not supposed to raise, but a leaked hold on the path where
    one does would be a permanent denial of that peer to that human."""
    from src.api.dispatch import _INFLIGHT

    monkeypatch.setattr("src.api.dispatch.record_consult", lambda *a, **kw: None)

    def exploding(req, port, key, post, **kw):
        raise RuntimeError("driver bug")

    monkeypatch.setattr("src.api.dispatch.backend_for", lambda mode: exploding)

    with pytest.raises(RuntimeError):
        _consult(client)

    assert _INFLIGHT.depth() == 0

    monkeypatch.setattr("src.api.dispatch.backend_for", lambda mode: _granting)
    assert _consult(client)["ok"] is True


def test_a_refused_consult_is_still_audited(client, monkeypatch):
    recorded = []
    monkeypatch.setattr("src.api.dispatch.record_consult",
                        lambda req, result, *a, **kw: recorded.append(result))

    def reentrant_driver(req, port, key, post, **kw):
        from src.dispatch.types import ConsultResult

        _consult(client)
        return ConsultResult.granted("sure", peer=req.to_agent)

    monkeypatch.setattr("src.api.dispatch.backend_for", lambda mode: reentrant_driver)

    _consult(client)

    assert any(r.reason == "cap_exceeded" for r in recorded)
