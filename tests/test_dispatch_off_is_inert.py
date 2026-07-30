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
    """Pins that the MODE_OFF guard in src/api/dispatch.py:154-163 short-circuits
    before any driver is resolved.

    The roster and port lookups are patched to succeed -- a real, consultable
    peer -- exactly as the sibling happy-path tests in test_api_dispatch.py do,
    so the *only* thing standing between this request and a driver call is the
    off-mode guard. Mocking `_post` to explode is NOT a valid way to prove this:
    `consult_off` never calls `post` at all (see src/dispatch/backends.py), so
    that mock can never fire regardless of whether the guard exists. Asserting
    `backend_for` itself was never invoked is the only thing only the guard can
    produce -- verified by deleting the guard block, see task-8-report.md.
    """
    from src.api.main import create_app
    from src.dispatch.types import Teammate

    monkeypatch.setattr("src.api.dispatch.get_session_owner", lambda a, s: "u-1")
    monkeypatch.setattr("src.api.dispatch.resolve_tier", lambda i, u: "account_admin")
    monkeypatch.setattr("src.api.dispatch.record_consult", lambda *a, **kw: None)
    monkeypatch.setattr(
        "src.api.dispatch.build_roster",
        lambda *a, **kw: [Teammate("karl-m", "K", None, "gpt-5.6-terra", "fast", True)],
    )
    monkeypatch.setattr("src.api.dispatch.port_for", lambda agent, entries: 8643)

    called = []
    monkeypatch.setattr("src.api.dispatch.backend_for", lambda mode: called.append(mode))

    r = TestClient(create_app()).post("/v1/dispatch/consult", headers=AUTH, json={
        "from_agent": "billie", "session_id": "s1",
        "to_agent": "karl-m", "question": "q",
    })

    assert r.json()["ok"] is False
    assert r.json()["reason"] == "not_enabled"
    assert called == []


def test_teammates_in_off_mode_enumerates_nothing(monkeypatch, fake_env):
    """The consult path was pinned here; the listing was not, and it never
    consulted current_mode() at all. In off mode it built the full roster and
    stamped "mode": "off" on it — so any holder of the shared ORCHESTRATOR_KEY,
    which is every profile on the box, could enumerate every agent's id,
    display name, subtitle and speed class on a box where dispatch was
    supposedly disabled.

    Same discipline as the consult test above: build_roster is patched to
    return a real peer, so the ONLY thing standing between this request and
    that peer's details in the response body is the off-mode guard. Verified by
    deleting the `if mode == MODE_OFF:` block in teammates() — this test then
    fails on the roster contents.
    """
    from src.api.main import create_app
    from src.dispatch.types import Teammate

    monkeypatch.setattr("src.api.dispatch.get_session_owner", lambda a, s: "u-1")
    monkeypatch.setattr("src.api.dispatch.resolve_tier", lambda i, u: "account_admin")
    monkeypatch.setattr(
        "src.api.dispatch.build_roster",
        lambda *a, **kw: [Teammate("karl-m", "Karl M", "Email ops",
                                   "gpt-5.6-terra", "fast", True, scope="user")],
    )

    r = TestClient(create_app()).get(
        "/v1/dispatch/teammates?agent=billie&session_id=s1", headers=AUTH)

    body = r.json()
    assert body["ok"] is False
    assert body["reason"] == "not_enabled"
    assert body["teammates"] == []
    # Not one field of the real roster may appear — not even the count.
    assert "karl-m" not in str(body)
    assert "Karl M" not in str(body)
    assert "Email ops" not in str(body)


def test_teammates_in_off_mode_resolves_no_provenance(monkeypatch, fake_env):
    """Off is inert end to end: the mode check comes before provenance
    resolution, so a disabled box makes no Supabase call for a listing either.
    Deleting the off-mode guard makes get_session_owner fire and this fails.
    """
    from src.api.main import create_app

    looked_up = []
    monkeypatch.setattr("src.api.dispatch.get_session_owner",
                        lambda a, s: looked_up.append((a, s)))
    monkeypatch.setattr("src.api.dispatch.build_roster", lambda *a, **kw: [])

    TestClient(create_app()).get(
        "/v1/dispatch/teammates?agent=billie&session_id=s1", headers=AUTH)

    assert looked_up == []
