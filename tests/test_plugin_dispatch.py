import json

import pytest

from plugins.dispatch.provider import DispatchProvider


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_URL", "http://127.0.0.1:9123")
    monkeypatch.setenv("ORCHESTRATOR_KEY", "topsecret")
    monkeypatch.setenv("DISPATCH_AGENT_ID", "billie")
    p = DispatchProvider()
    p.initialize("sess-1")
    return p


def test_off_mode_exposes_no_tools_and_no_prompt_block(provider, monkeypatch):
    monkeypatch.setenv("DISPATCH_MODE", "off")

    assert provider.get_tool_schemas() == []
    assert provider.system_prompt_block() == ""


def test_direct_mode_exposes_exactly_the_two_tools(provider, monkeypatch):
    monkeypatch.setenv("DISPATCH_MODE", "direct")

    names = sorted(t["name"] for t in provider.get_tool_schemas())

    assert names == ["ask_teammate", "list_teammates"]


def test_prompt_block_forbids_fabricating_and_claiming_handoff(provider, monkeypatch):
    monkeypatch.setenv("DISPATCH_MODE", "direct")

    block = provider.system_prompt_block().lower()

    assert "never" in block
    assert "invent" in block or "fabricate" in block
    assert "handed" in block or "assigned" in block


def test_initialize_captures_the_session_id(provider):
    assert provider._session_id == "sess-1"


def test_ask_teammate_sends_agent_and_session_and_returns_the_answer(provider, monkeypatch):
    monkeypatch.setenv("DISPATCH_MODE", "direct")
    sent = {}

    def fake_post(path, payload):
        sent["path"] = path
        sent["payload"] = payload
        return {"ok": True, "answer": "shorten it", "reason": None,
                "detail": "", "peer": "karl-m"}

    monkeypatch.setattr(provider._client, "post", fake_post)

    out = provider.handle_tool_call(
        "ask_teammate", {"teammate": "karl-m", "question": "subject ok?"}
    )

    assert sent["payload"]["from_agent"] == "billie"
    assert sent["payload"]["session_id"] == "sess-1"
    assert "shorten it" in out


def test_refusal_is_surfaced_verbatim_and_never_as_an_answer(provider, monkeypatch):
    monkeypatch.setenv("DISPATCH_MODE", "direct")
    monkeypatch.setattr(
        provider._client, "post",
        lambda path, payload: {"ok": False, "answer": None, "reason": "timeout",
                               "detail": "karl-m did not answer in 30s",
                               "peer": "karl-m"},
    )

    out = provider.handle_tool_call("ask_teammate", {"teammate": "karl-m",
                                                    "question": "q"})

    assert "timeout" in out
    assert "did not answer" in out


def test_transport_failure_becomes_a_structured_refusal_not_an_exception(provider, monkeypatch):
    monkeypatch.setenv("DISPATCH_MODE", "direct")

    def boom(path, payload):
        raise RuntimeError("orchestrator unreachable")

    monkeypatch.setattr(provider._client, "post", boom)

    out = provider.handle_tool_call("ask_teammate", {"teammate": "karl-m",
                                                    "question": "q"})

    assert "orchestrator unreachable" in out
    payload = json.loads(out) if out.strip().startswith("{") else {"raw": out}
    assert "ok" not in payload or payload.get("ok") is False


def test_unknown_tool_name_is_reported_not_raised(provider):
    assert "unknown" in provider.handle_tool_call("nope", {}).lower()


def test_config_schema_exposes_mode_and_orchestrator_url(provider):
    keys = {f["key"] for f in provider.get_config_schema()}

    assert "mode" in keys
    assert "orchestrator_url" in keys
