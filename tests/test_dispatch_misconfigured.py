"""A misconfigured orchestrator must not be reported as a broken peer.

scripts/install.sh writes `HERMES_GATEWAY_KEY=` blank into the orchestrator env
for the operator to paste into later. On a box where that never happened, every
consult sent `Authorization: Bearer ` to the peer, got a 401, and reported
`peer_unavailable` — which the runbook defines as "the peer's gateway could not
be reached at all". The operator goes and checks karl's gateway. It is running.
"""
import logging

import pytest

from src.dispatch.backends import consult_direct
from src.dispatch.types import (
    REASON_MISCONFIGURED,
    REASON_PEER_UNAVAILABLE,
    ConsultRequest,
)

REQ = ConsultRequest("billie", "sess-1", "karl-m", "does this subject line work?")


def _explode(url, headers, json, timeout):
    raise AssertionError("no request may be sent without a gateway key")


def test_a_blank_gateway_key_refuses_before_contacting_the_peer():
    """Follows src/persona_polish.py:38-39's shape: return early rather than
    send a bearer-less Authorization header."""
    r = consult_direct(REQ, 8643, "", post=_explode)

    assert r.ok is False
    assert r.reason == REASON_MISCONFIGURED
    assert r.answer is None


def test_the_refusal_points_at_configuration_not_at_the_peer():
    r = consult_direct(REQ, 8643, "", post=_explode)

    assert r.reason != REASON_PEER_UNAVAILABLE
    assert "HERMES_GATEWAY_KEY" in r.detail


def test_a_whitespace_only_key_is_treated_as_blank():
    """_gateway_key() strips (matching src/api/runs.py:179), so a key pasted
    with a trailing newline arrives here as empty rather than as ' '."""
    from src.api.dispatch import _gateway_key

    import os

    os.environ["HERMES_GATEWAY_KEY"] = "  \n "
    try:
        assert _gateway_key() == ""
    finally:
        os.environ.pop("HERMES_GATEWAY_KEY", None)


def test_a_real_key_is_stripped_of_a_pasted_newline(monkeypatch):
    from src.api.dispatch import _gateway_key

    monkeypatch.setenv("HERMES_GATEWAY_KEY", "gwkey\n")

    assert _gateway_key() == "gwkey"


def test_a_present_key_still_reaches_the_peer():
    """The other half: the guard must not refuse a correctly configured box."""
    sent = {}

    def post(url, headers, json, timeout):
        sent["auth"] = headers["Authorization"]
        return {"choices": [{"message": {"content": "yes"}}]}

    r = consult_direct(REQ, 8643, "gwkey", post=post)

    assert r.ok is True
    assert sent["auth"] == "Bearer gwkey"


# --- deferred minor #5: peer-side failures must leave a trace ----------------

def test_a_peer_failure_is_logged_not_just_returned(caplog):
    """The except branch was the only place a peer-side failure occurred and it
    left no trace anywhere, which is exactly what made the client-timeout and
    blank-key failures undiagnosable from logs."""
    def post(url, headers, json, timeout):
        raise RuntimeError("connection refused")

    with caplog.at_level(logging.WARNING, logger="src.dispatch.backends"):
        r = consult_direct(REQ, 8643, "k", post=post)

    assert r.reason == REASON_PEER_UNAVAILABLE
    assert any("karl-m" in rec.getMessage() for rec in caplog.records)


def test_a_peer_timeout_is_logged(caplog):
    def post(url, headers, json, timeout):
        raise TimeoutError("read timeout")

    with caplog.at_level(logging.WARNING, logger="src.dispatch.backends"):
        consult_direct(REQ, 8643, "k", post=post)

    assert caplog.records


def test_the_log_names_the_peer_and_the_port(caplog):
    def post(url, headers, json, timeout):
        raise RuntimeError("boom")

    with caplog.at_level(logging.WARNING, logger="src.dispatch.backends"):
        consult_direct(REQ, 8643, "k", post=post)

    rendered = " ".join(rec.getMessage() for rec in caplog.records)
    assert "karl-m" in rendered
    assert "8643" in rendered


@pytest.mark.parametrize("key", ["", "   ", "\n"])
def test_every_blank_shape_refuses_without_a_request(key):
    """consult_direct receives the already-stripped value from _gateway_key(),
    but must itself be safe against any falsy-after-strip input."""
    r = consult_direct(REQ, 8643, key.strip(), post=_explode)

    assert r.reason == REASON_MISCONFIGURED
