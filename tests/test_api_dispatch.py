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
    assert client.get(
        "/v1/dispatch/teammates?agent=billie&session_id=s1").status_code in (401, 403)


def test_teammates_lists_peers_with_eligibility(client, monkeypatch):
    from src.dispatch.types import Teammate

    monkeypatch.setattr(
        "src.api.dispatch.build_roster",
        lambda *a, **kw: [Teammate("karl-m", "Karl M", "Email", "gpt-5.6-terra",
                                   "fast", True)],
    )

    r = client.get("/v1/dispatch/teammates?agent=billie&session_id=s1", headers=AUTH)

    assert r.status_code == 200
    assert r.json()["ok"] is True
    body = r.json()["teammates"]
    assert body[0]["agent_id"] == "karl-m"
    assert body[0]["consult_eligible"] is True


def test_teammates_without_provenance_lists_nothing(client, monkeypatch):
    """The listing is provenance-gated exactly like a consult: without a
    resolvable human there is no tier to filter the roster by, and an
    unfiltered roster is the enumeration this endpoint used to permit."""
    from src.dispatch.types import Teammate

    monkeypatch.setattr("src.api.dispatch.get_session_owner", lambda a, s: None)
    monkeypatch.setattr(
        "src.api.dispatch.build_roster",
        lambda *a, **kw: [Teammate("karl-m", "Karl M", "Email", "gpt-5.6-terra",
                                   "fast", True)],
    )

    r = client.get("/v1/dispatch/teammates?agent=billie&session_id=bogus",
                   headers=AUTH)

    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert r.json()["reason"] == "forbidden"
    assert r.json()["teammates"] == []


def test_consult_returns_the_peer_answer(client, monkeypatch):
    from src.dispatch.types import ConsultResult, Teammate

    monkeypatch.setattr(
        "src.api.dispatch.build_roster",
        lambda *a, **kw: [Teammate("karl-m", "K", None, "gpt-5.6-terra", "fast", True)],
    )
    monkeypatch.setattr("src.api.dispatch.port_for", lambda agent, entries: 8643)
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
    """Fail-closed at the API boundary: no provenance, no dispatch.

    build_roster and port_for are patched to succeed (a real, consultable peer),
    exactly as the sibling happy-path tests do, so the *only* thing standing between
    this request and a backend call is the unresolvable-origin guard. Without that —
    verified by temporarily commenting out the `if origin is None:` block in
    src/api/dispatch.py — check() passes (authority.check never reads origin),
    port_for resolves, and backend_for is reached: this test then fails on
    `called == []`. With the guard restored it passes again.
    """
    from src.dispatch.types import Teammate

    called = []
    monkeypatch.setattr("src.api.dispatch.get_session_owner", lambda a, s: None)
    monkeypatch.setattr(
        "src.api.dispatch.build_roster",
        lambda *a, **kw: [Teammate("karl-m", "K", None, "gpt-5.6-terra", "fast", True)],
    )
    monkeypatch.setattr("src.api.dispatch.port_for", lambda agent, entries: 8643)
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
    monkeypatch.setattr("src.api.dispatch.port_for", lambda agent, entries: 8643)
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


def test_roster_resolves_from_config_default_when_env_var_unset(fake_env, monkeypatch):
    """dispatch must read cfg.hermes_stack_dir (resolved once by Config.load()), not
    os.environ["HERMES_STACK_DIR"] directly. HERMES_STACK_DIR is a documented-optional
    override (.env.example) and Config.load() already defaults hermes_stack_dir to
    $HOME/hermes-stack when it's unset (src/config.py:27) — fake_env's stack fixture
    lives at exactly that default path, with a real AGENTS_JSON=[] .env inside it.

    Reading the env var directly (dispatch.py's original _env_path) instead resolves
    a relative, nonexistent ".env" path on a default install and raises
    FileNotFoundError -- an unhandled 500. This test builds its own app AFTER
    deleting the var, so Config.load() (called inside create_app()) computes the
    default itself; using the shared `client` fixture would not catch this, since
    that fixture's app is already built (and its config already resolved) with the
    var still set.
    """
    monkeypatch.delenv("HERMES_STACK_DIR", raising=False)
    from src.api.main import create_app

    local_client = TestClient(create_app())

    r = local_client.get("/v1/dispatch/teammates?agent=billie&session_id=s1",
                         headers=AUTH)

    assert r.status_code == 200
    assert r.json()["teammates"] == []


def test_unimplemented_mode_is_refused_not_500(client, monkeypatch):
    """DISPATCH_MODE=local/linear are VALID_MODES (src/dispatch/types.py) with no
    backend driver yet (src/dispatch/backends.py only wires off and direct). That
    must become a structured not_enabled refusal, not an unhandled 500 -- and must
    never reach port_for, since the mode is checked before any roster/port lookup.
    """
    from src.dispatch.types import Teammate

    monkeypatch.setenv("DISPATCH_MODE", "local")
    monkeypatch.setattr(
        "src.api.dispatch.build_roster",
        lambda *a, **kw: [Teammate("karl-m", "K", None, "gpt-5.6-terra", "fast", True)],
    )
    called = []
    monkeypatch.setattr("src.api.dispatch.port_for",
                        lambda agent, entries: called.append(agent) or 8643)

    r = client.post("/v1/dispatch/consult", headers=AUTH, json={
        "from_agent": "billie", "session_id": "s1",
        "to_agent": "karl-m", "question": "q",
    })

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["reason"] == "not_enabled"
    assert called == []
