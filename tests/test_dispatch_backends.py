import pytest

from src.dispatch.backends import backend_for, consult_direct, consult_off
from src.dispatch.types import (
    MODE_DIRECT,
    MODE_OFF,
    REASON_NOT_ENABLED,
    REASON_PEER_UNAVAILABLE,
    REASON_TIMEOUT,
    ConsultRequest,
)

REQ = ConsultRequest("billie", "sess-1", "karl-m", "does this subject line work?")


def _reply(text):
    return {"choices": [{"message": {"content": text}}]}


def test_off_backend_refuses_without_calling_anything():
    calls = []

    r = consult_off(REQ, 8643, "k", post=lambda *a, **kw: calls.append(a))

    assert r.ok is False
    assert r.reason == REASON_NOT_ENABLED
    assert calls == []


def test_direct_backend_returns_the_peer_reply():
    def post(url, headers, json, timeout):
        assert url == "http://127.0.0.1:8643/v1/chat/completions"
        assert headers["Authorization"] == "Bearer gwkey"
        assert REQ.question in json["messages"][0]["content"]
        assert json["stream"] is False
        return _reply("Yes, but shorten it.")

    r = consult_direct(REQ, 8643, "gwkey", post=post)

    assert r.ok is True
    assert r.answer == "Yes, but shorten it."
    assert r.peer == "karl-m"


def test_direct_names_the_asking_agent_so_the_peer_knows_who_is_asking():
    seen = {}

    def post(url, headers, json, timeout):
        seen["content"] = json["messages"][0]["content"]
        return _reply("ok")

    consult_direct(REQ, 8643, "k", post=post)

    assert "billie" in seen["content"]


def test_direct_timeout_is_a_structured_refusal_not_an_exception():
    def post(url, headers, json, timeout):
        raise TimeoutError("read timeout")

    r = consult_direct(REQ, 8643, "k", post=post)

    assert r.ok is False
    assert r.reason == REASON_TIMEOUT
    assert r.answer is None


def test_direct_peer_error_is_a_structured_refusal():
    def post(url, headers, json, timeout):
        raise RuntimeError("connection refused")

    r = consult_direct(REQ, 8643, "k", post=post)

    assert r.ok is False
    assert r.reason == REASON_PEER_UNAVAILABLE
    assert "connection refused" in r.detail


def test_direct_malformed_reply_is_a_refusal_not_a_crash():
    """A gateway that returns an unexpected shape must not fabricate an answer."""
    r = consult_direct(REQ, 8643, "k", post=lambda *a, **kw: {"unexpected": True})

    assert r.ok is False
    assert r.answer is None


def test_direct_empty_reply_is_a_refusal():
    r = consult_direct(REQ, 8643, "k", post=lambda *a, **kw: _reply("   "))

    assert r.ok is False
    assert r.answer is None


def test_backend_for_maps_modes():
    assert backend_for(MODE_OFF) is consult_off
    assert backend_for(MODE_DIRECT) is consult_direct


def test_backend_for_rejects_unknown_and_unimplemented_modes():
    with pytest.raises(ValueError):
        backend_for("local")
    with pytest.raises(ValueError):
        backend_for("nonsense")
