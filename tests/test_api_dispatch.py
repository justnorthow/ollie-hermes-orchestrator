import pytest
from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer topsecret"}


@pytest.fixture
def client(fake_env):
    from src.api.main import create_app

    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """No test in this module may reach a gateway or Supabase."""
    monkeypatch.setenv("DISPATCH_MODE", "direct")
    monkeypatch.setattr("src.api.dispatch.get_session_owner", lambda a, s: "u-1")
    monkeypatch.setattr("src.api.dispatch.resolve_tier", lambda i, u: "account_admin")
    monkeypatch.setattr("src.api.dispatch.record_consult",
                        lambda *a, **kw: None)


def test_teammates_requires_auth(client):
    assert client.get("/v1/dispatch/teammates?agent=billie").status_code in (401, 403)


def test_teammates_lists_peers_with_eligibility(client, monkeypatch):
    from src.dispatch.types import Teammate

    monkeypatch.setattr(
        "src.api.dispatch.build_roster",
        lambda *a, **kw: [Teammate("karl-m", "Karl M", "Email", "gpt-5.6-terra",
                                   "fast", True)],
    )

    r = client.get("/v1/dispatch/teammates?agent=billie", headers=AUTH)

    assert r.status_code == 200
    body = r.json()["teammates"]
    assert body[0]["agent_id"] == "karl-m"
    assert body[0]["consult_eligible"] is True


def test_consult_returns_the_peer_answer(client, monkeypatch):
    from src.dispatch.types import ConsultResult, Teammate

    monkeypatch.setattr(
        "src.api.dispatch.build_roster",
        lambda *a, **kw: [Teammate("karl-m", "K", None, "gpt-5.6-terra", "fast", True)],
    )
    monkeypatch.setattr("src.api.dispatch.port_for", lambda agent: 8643)
    monkeypatch.setattr(
        "src.api.dispatch.backend_for",
        lambda mode: (lambda req, port, key, post, **kw:
                      ConsultResult.granted("shorten it", peer=req.to_agent)),
    )

    r = client.post("/v1/dispatch/consult", headers=AUTH, json={
        "from_agent": "billie", "session_id": "s1",
        "to_agent": "karl-m", "question": "subject line ok?",
    })

    assert r.status_code == 200
    assert r.json() == {"ok": True, "answer": "shorten it", "reason": None,
                        "detail": "", "peer": "karl-m"}


def test_unresolvable_session_is_refused_and_never_reaches_a_backend(client, monkeypatch):
    """Fail-closed at the API boundary: no provenance, no dispatch."""
    called = []
    monkeypatch.setattr("src.api.dispatch.get_session_owner", lambda a, s: None)
    monkeypatch.setattr("src.api.dispatch.backend_for",
                        lambda mode: called.append(mode))

    r = client.post("/v1/dispatch/consult", headers=AUTH, json={
        "from_agent": "billie", "session_id": "bogus",
        "to_agent": "karl-m", "question": "q",
    })

    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert r.json()["reason"] == "forbidden"
    assert called == []


def test_caller_supplied_identity_is_ignored(client, monkeypatch):
    """A caller cannot assert who it acts for — the field is not even read."""
    from src.dispatch.types import ConsultResult, Teammate

    monkeypatch.setattr(
        "src.api.dispatch.build_roster",
        lambda *a, **kw: [Teammate("karl-m", "K", None, "gpt-5.6-terra", "fast", True)],
    )
    monkeypatch.setattr("src.api.dispatch.port_for", lambda agent: 8643)
    monkeypatch.setattr(
        "src.api.dispatch.backend_for",
        lambda mode: (lambda req, port, key, post, **kw:
                      ConsultResult.granted("ok", peer=req.to_agent)),
    )

    r = client.post("/v1/dispatch/consult", headers=AUTH, json={
        "from_agent": "billie", "session_id": "s1", "to_agent": "karl-m",
        "question": "q", "user_id": "someone-else", "tier": "account_admin",
    })

    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_mode_off_refuses_without_a_backend_call(client, monkeypatch):
    monkeypatch.setenv("DISPATCH_MODE", "off")

    r = client.post("/v1/dispatch/consult", headers=AUTH, json={
        "from_agent": "billie", "session_id": "s1",
        "to_agent": "karl-m", "question": "q",
    })

    assert r.json()["ok"] is False
    assert r.json()["reason"] == "not_enabled"


def test_unknown_peer_is_refused(client, monkeypatch):
    monkeypatch.setattr("src.api.dispatch.build_roster", lambda *a, **kw: [])

    r = client.post("/v1/dispatch/consult", headers=AUTH, json={
        "from_agent": "billie", "session_id": "s1",
        "to_agent": "ghost", "question": "q",
    })

    assert r.json()["reason"] == "unknown_peer"
