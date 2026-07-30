"""Dispatch must not be a lateral path around human->agent access control.

The failure this pins: karl-m is scope="company", manager_visible=False, so
src/api/authz.py::can_reach lets only account_admin and above reach him — the
dashboard hides him from a member, and a direct session read 403s. Without the
roster filter, that member could ask billie "ask karl what our margin is",
provenance would resolve them correctly at tier member, and karl's answer would
come back to them verbatim. The listing leaked karl's id, display name and
subtitle on top of that.
"""
import pytest
from fastapi.testclient import TestClient

from src.dispatch.roster import build_roster, visible_to
from src.dispatch.types import Teammate

AUTH = {"Authorization": "Bearer topsecret"}

# karl-m: company scope, not manager-visible -> account_admin+ only.
KARL = Teammate("karl-m", "Karl M", "Email ops", "gpt-5.6-terra", "fast", True,
                scope="company", manager_visible=False)
# ollie: user scope -> every authenticated tier.
OLLIE = Teammate("default", "Ollie", "Assistant", "gpt-5.6-terra", "fast", True,
                 scope="user", manager_visible=False)


@pytest.fixture
def client(fake_env):
    from src.api.main import create_app

    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setenv("DISPATCH_MODE", "direct")
    monkeypatch.setattr("src.api.dispatch.get_session_owner", lambda a, s: "u-1")
    monkeypatch.setattr("src.api.dispatch.record_consult", lambda *a, **kw: None)
    monkeypatch.setattr(
        "src.api.dispatch.build_roster", lambda *a, **kw: [KARL, OLLIE])
    monkeypatch.setattr("src.api.dispatch.port_for", lambda agent, entries: 8643)


def _as_tier(monkeypatch, tier):
    monkeypatch.setattr("src.api.dispatch.resolve_tier", lambda i, u: tier)


def _consult(client, to_agent="karl-m"):
    return client.post("/v1/dispatch/consult", headers=AUTH, json={
        "from_agent": "billie", "session_id": "s1",
        "to_agent": to_agent, "question": "what is our margin on that deal?",
    }).json()


# --- the pure filter ---------------------------------------------------------

def test_visible_to_hides_an_out_of_reach_peer():
    from src.api.authz import can_reach

    assert visible_to([KARL, OLLIE], "member", can_reach) == [OLLIE]
    assert visible_to([KARL, OLLIE], "account_admin", can_reach) == [KARL, OLLIE]


def test_build_roster_carries_scope_and_manager_visibility():
    """The filter is only as good as the data reaching it. If build_roster
    dropped these fields, Teammate's fail-closed defaults would hide every
    company-scope peer from everyone below account_admin — safe, but the test
    below would stop proving that an admin CAN reach karl."""
    from dataclasses import dataclass

    @dataclass
    class _Entry:
        id: str
        name: str
        model: str
        scope: str
        manager_visible: bool

    roster = build_roster(
        [_Entry("karl-m", "Karl M", "gpt-5.6-terra", "company", True)],
        [{"id": "gpt-5.6-terra", "speed_class": "fast"}],
        self_agent="billie",
    )

    assert roster[0].scope == "company"
    assert roster[0].manager_visible is True


def test_an_entry_missing_the_fields_fails_closed():
    from dataclasses import dataclass

    from src.api.authz import can_reach

    @dataclass
    class _Bare:
        id: str
        name: str
        model: str

    roster = build_roster([_Bare("mystery", "Mystery", "gpt-5.6-terra")],
                          [{"id": "gpt-5.6-terra", "speed_class": "fast"}],
                          self_agent="billie")

    assert visible_to(roster, "member", can_reach) == []
    assert visible_to(roster, "manager", can_reach) == []


# --- the consult path --------------------------------------------------------

def test_member_cannot_consult_an_out_of_reach_peer(client, monkeypatch):
    """An agent's authority is its human's authority. Deleting the visible_to()
    call in src/api/dispatch.py::_roster_for makes this test fail with
    ok is True.

    The backend is patched to GRANT. Leaving the real driver in place would let
    this test pass without the filter for the wrong reason — the driver would
    fail to connect to the fake port and refuse with peer_unavailable, so the
    assertion would hold whether or not the human was ever allowed to ask.
    """
    from src.dispatch.types import ConsultResult

    _as_tier(monkeypatch, "member")
    monkeypatch.setattr(
        "src.api.dispatch.backend_for",
        lambda mode: (lambda req, port, key, post, **kw:
                      ConsultResult.granted("about 18 points", peer=req.to_agent)),
    )

    body = _consult(client)

    assert body["ok"] is False
    assert body["answer"] is None


def test_the_refusal_does_not_confirm_the_peer_exists(client, monkeypatch):
    """unknown_peer, NOT a new reason of its own. A distinct 'out of reach'
    reason would tell a member that karl-m exists — precisely what
    src/api/authz.py:50's 'unknown agent — fail closed, don't leak existence'
    is there to prevent."""
    _as_tier(monkeypatch, "member")

    body = _consult(client)

    assert body["reason"] == "unknown_peer"
    assert _consult(client, to_agent="no-such-agent")["reason"] == "unknown_peer"


def test_account_admin_can_consult_the_same_peer(client, monkeypatch):
    """The other half: the filter must not deny someone who is allowed. Without
    this, a filter that returned [] always would pass the test above."""
    from src.dispatch.types import ConsultResult

    _as_tier(monkeypatch, "account_admin")
    monkeypatch.setattr(
        "src.api.dispatch.backend_for",
        lambda mode: (lambda req, port, key, post, **kw:
                      ConsultResult.granted("about 18 points", peer=req.to_agent)),
    )

    body = _consult(client)

    assert body["ok"] is True
    assert body["answer"] == "about 18 points"


def test_a_member_may_still_consult_a_user_scoped_peer(client, monkeypatch):
    from src.dispatch.types import ConsultResult

    _as_tier(monkeypatch, "member")
    monkeypatch.setattr(
        "src.api.dispatch.backend_for",
        lambda mode: (lambda req, port, key, post, **kw:
                      ConsultResult.granted("sure", peer=req.to_agent)),
    )

    assert _consult(client, to_agent="default")["ok"] is True


def test_an_out_of_reach_peer_never_reaches_a_backend(client, monkeypatch):
    """The refusal must happen before the peer's gateway is touched — otherwise
    the peer has already generated (and been paid for) an answer the human was
    never allowed to ask for."""
    _as_tier(monkeypatch, "member")
    called = []

    def driver(req, port, key, post, **kw):
        called.append(req.to_agent)
        raise AssertionError("an out-of-reach peer must never be contacted")

    # backend_for itself IS called earlier (dispatch.py resolves the driver
    # before any roster lookup, so an unimplemented mode refuses before a port
    # lookup). What must not happen is the driver being invoked.
    monkeypatch.setattr("src.api.dispatch.backend_for", lambda mode: driver)

    _consult(client)

    assert called == []


# --- the listing -------------------------------------------------------------

def test_out_of_reach_peer_is_absent_from_list_teammates(client, monkeypatch):
    """list_teammates handed the calling agent karl's agent_id, display_name
    and subtitle regardless of who was asking."""
    _as_tier(monkeypatch, "member")

    body = client.get("/v1/dispatch/teammates?agent=billie&session_id=s1",
                      headers=AUTH).json()

    ids = [t["agent_id"] for t in body["teammates"]]
    assert ids == ["default"]
    assert "Karl M" not in str(body)
    assert "Email ops" not in str(body)


def test_account_admin_sees_the_full_roster(client, monkeypatch):
    _as_tier(monkeypatch, "account_admin")

    body = client.get("/v1/dispatch/teammates?agent=billie&session_id=s1",
                      headers=AUTH).json()

    assert sorted(t["agent_id"] for t in body["teammates"]) == ["default", "karl-m"]


def test_manager_visibility_is_honoured(client, monkeypatch):
    """manager_visible is the third input to can_reach, and a filter that read
    only scope and tier would pass every other test in this file."""
    _as_tier(monkeypatch, "manager")
    visible = Teammate("shared", "Shared", None, "gpt-5.6-terra", "fast", True,
                       scope="company", manager_visible=True)
    monkeypatch.setattr("src.api.dispatch.build_roster",
                        lambda *a, **kw: [KARL, visible])

    body = client.get("/v1/dispatch/teammates?agent=billie&session_id=s1",
                      headers=AUTH).json()

    assert [t["agent_id"] for t in body["teammates"]] == ["shared"]
