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


def test_unknown_tool_name_is_reported_not_raised(provider, monkeypatch):
    monkeypatch.setenv("DISPATCH_MODE", "direct")

    assert "unknown" in provider.handle_tool_call("nope", {}).lower()


def test_config_schema_is_empty_because_nothing_reads_it(provider):
    """The schema used to advertise `mode` and `orchestrator_url`. Neither was
    ever read — `_mode()` reads DISPATCH_MODE from the environment on every
    call, and DispatchHttpClient reads ORCHESTRATOR_URL/ORCHESTRATOR_KEY from
    the environment at construction. A host UI writing those keys would change
    nothing while appearing to work, so they are gone rather than decorative."""
    assert provider.get_config_schema() == []


def test_list_teammates_success_carries_ok_like_its_failures_do(provider, monkeypatch):
    """One tool must not answer in two incompatible shapes. The success payload
    had no `ok` field while the failure payload did, and the system prompt at
    provider.py's _PROMPT_BLOCK instructs the model to distinguish refusals
    from answers."""
    monkeypatch.setenv("DISPATCH_MODE", "direct")
    monkeypatch.setattr(
        provider._client, "get",
        lambda path, params: {"ok": True, "mode": "direct",
                              "teammates": [{"agent_id": "karl-m"}]},
    )

    body = json.loads(provider.handle_tool_call("list_teammates", {}))

    assert body["ok"] is True
    assert body["teammates"] == [{"agent_id": "karl-m"}]


def test_a_server_that_predates_the_ok_field_is_filled_in(provider, monkeypatch):
    """Plugin and orchestrator are deployed separately and can skew. A 200 with
    a listing but no `ok` is a success, so it is filled in rather than handed
    to the model in a shape the prompt never taught it to read."""
    monkeypatch.setenv("DISPATCH_MODE", "direct")
    monkeypatch.setattr(
        provider._client, "get",
        lambda path, params: {"mode": "direct", "teammates": []},
    )

    assert json.loads(provider.handle_tool_call("list_teammates", {}))["ok"] is True


def test_a_server_refusal_is_never_overwritten_as_ok(provider, monkeypatch):
    """The fill-in must only apply when `ok` is absent. Overriding a server's
    `ok: false` would turn a refusal into an answer, the one failure this whole
    design exists to prevent."""
    monkeypatch.setenv("DISPATCH_MODE", "direct")
    monkeypatch.setattr(
        provider._client, "get",
        lambda path, params: {"ok": False, "mode": "off", "teammates": [],
                              "reason": "not_enabled"},
    )

    body = json.loads(provider.handle_tool_call("list_teammates", {}))

    assert body["ok"] is False
    assert body["reason"] == "not_enabled"


def test_list_teammates_sends_the_session_id(provider, monkeypatch):
    """The orchestrator filters the roster by the human resolved from this
    session. Without it there is no tier and the listing is empty."""
    monkeypatch.setenv("DISPATCH_MODE", "direct")
    sent = {}

    def fake_get(path, params):
        sent["params"] = params
        return {"ok": True, "teammates": []}

    monkeypatch.setattr(provider._client, "get", fake_get)
    provider.handle_tool_call("list_teammates", {})

    assert sent["params"] == {"agent": "billie", "session_id": "sess-1"}


def test_handle_tool_call_off_mode_refuses_without_any_http_call(provider, monkeypatch):
    monkeypatch.setenv("DISPATCH_MODE", "off")
    calls = []
    monkeypatch.setattr(
        provider._client, "post",
        lambda path, payload: calls.append(payload),
    )

    out = provider.handle_tool_call(
        "ask_teammate", {"teammate": "karl-m", "question": "q"}
    )

    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["reason"] == "not_enabled"
    assert calls == []


def test_unimplemented_mode_behaves_as_off_from_the_plugin_side(provider, monkeypatch):
    # DISPATCH_MODE="local" is a VALID_MODES value server-side (src/dispatch/types.py)
    # but has no backend driver wired yet, and a typo like "dryct" is not a mode at
    # all. Both must leave the plugin fully inert, exactly like "off" -- otherwise
    # the agent is invited into a tool call that the server refuses every time.
    monkeypatch.setenv("DISPATCH_MODE", "local")

    assert provider.get_tool_schemas() == []
    assert provider.system_prompt_block() == ""
    assert provider.is_available() is False


def test_ask_teammate_with_no_agent_id_sends_an_empty_from_agent(provider, monkeypatch):
    monkeypatch.setenv("DISPATCH_MODE", "direct")
    monkeypatch.delenv("DISPATCH_AGENT_ID", raising=False)
    sent = {}

    def fake_post(path, payload):
        sent["payload"] = payload
        return {"ok": True, "answer": "x", "reason": None, "detail": "",
                "peer": "karl-m"}

    monkeypatch.setattr(provider._client, "post", fake_post)

    provider.handle_tool_call(
        "ask_teammate", {"teammate": "karl-m", "question": "q"}
    )

    # The plugin does not default or guess an agent id -- an unset
    # DISPATCH_AGENT_ID must reach the orchestrator as empty, not as something
    # plausible, so the server-side fail-closed provenance check is what stops
    # an unset id from being usable, not client-side leniency.
    assert sent["payload"]["from_agent"] == ""
