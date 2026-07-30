import pytest
from fastapi.testclient import TestClient

from plugins.dispatch.provider import DispatchProvider

AUTH = {"Authorization": "Bearer topsecret"}


@pytest.fixture(autouse=True)
def _off(monkeypatch):
    monkeypatch.setenv("DISPATCH_MODE", "off")
    monkeypatch.setenv("DISPATCH_AGENT_ID", "billie")


def test_plugin_contributes_nothing_to_the_model_context():
    """The acceptance test for existing customer boxes: with mode off, an agent's
    tool list and system prompt must be identical to a box without the plugin."""
    p = DispatchProvider()
    p.initialize("sess-1")

    assert p.get_tool_schemas() == []
    assert p.system_prompt_block() == ""
    assert p.is_available() is False


def test_unset_mode_defaults_to_off(monkeypatch):
    monkeypatch.delenv("DISPATCH_MODE", raising=False)
    p = DispatchProvider()

    assert p.get_tool_schemas() == []
    assert p.system_prompt_block() == ""


def test_unrecognised_mode_falls_back_to_off_at_the_api(monkeypatch, fake_env):
    from src.api.dispatch import current_mode

    monkeypatch.setenv("DISPATCH_MODE", "banana")

    assert current_mode() == "off"


def test_consult_in_off_mode_never_reaches_a_gateway(monkeypatch, fake_env):
    from src.api.main import create_app

    monkeypatch.setattr("src.api.dispatch.get_session_owner", lambda a, s: "u-1")
    monkeypatch.setattr("src.api.dispatch.resolve_tier", lambda i, u: "account_admin")
    monkeypatch.setattr("src.api.dispatch.record_consult", lambda *a, **kw: None)

    def explode(*a, **kw):
        raise AssertionError("off mode must not call a gateway")

    monkeypatch.setattr("src.api.dispatch._post", explode)

    r = TestClient(create_app()).post("/v1/dispatch/consult", headers=AUTH, json={
        "from_agent": "billie", "session_id": "s1",
        "to_agent": "karl-m", "question": "q",
    })

    assert r.json()["ok"] is False
    assert r.json()["reason"] == "not_enabled"
