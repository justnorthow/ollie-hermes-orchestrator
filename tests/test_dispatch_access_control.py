"""Any agent may consult any agent. The crossing is recorded, never blocked.

`scope` and `manager_visible` govern how a HUMAN reaches an agent: what appears
in their picker, and what a direct session read allows. Their purpose is to
spare a user from working out which specialist to go to — so they are
deliberately NOT a limit on which peers an agent may consult. A chief of staff
who could only reach the agents her human could already reach would push that
burden straight back onto the user.

An earlier version of this file pinned the opposite, on the reading that scope
was an authority boundary. It is not. What survives is observability: consults
are read-only, so the exposure is information the human could have asked their
agent to find anyway, but "did anyone reach something through an agent that
they could not reach themselves?" stays answerable, because the crossing is
stamped on the governance_events row.
"""
import pytest
from fastapi.testclient import TestClient

from src.api.authz import can_reach
from src.dispatch.roster import beyond_human_reach, build_roster
from src.dispatch.types import Teammate

AUTH = {"Authorization": "Bearer topsecret"}

# karl-m: company scope, not manager-visible -> a human needs account_admin+.
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
    monkeypatch.setattr(
        "src.api.dispatch.build_roster", lambda *a, **kw: [KARL, OLLIE])
    monkeypatch.setattr("src.api.dispatch.port_for", lambda agent, entries: 8643)


@pytest.fixture
def audited(monkeypatch):
    """Capture what reaches the audit sink."""
    rows = []
    monkeypatch.setattr("src.api.dispatch.record_consult",
                        lambda *a, **kw: rows.append(kw))
    return rows


def _as_tier(monkeypatch, tier):
    monkeypatch.setattr("src.api.dispatch.resolve_tier", lambda i, u: tier)


def _grants(monkeypatch, answer="about 18 points"):
    from src.dispatch.types import ConsultResult

    monkeypatch.setattr(
        "src.api.dispatch.backend_for",
        lambda mode: (lambda req, port, key, post, **kw:
                      ConsultResult.granted(answer, peer=req.to_agent)),
    )


def _consult(client, to_agent="karl-m"):
    return client.post("/v1/dispatch/consult", headers=AUTH, json={
        "from_agent": "billie", "session_id": "s1",
        "to_agent": to_agent, "question": "what is our margin on that deal?",
    }).json()


# --- the predicate ------------------------------------------------------------

def test_beyond_human_reach_identifies_the_crossing():
    assert beyond_human_reach(KARL, "member", can_reach) is True
    assert beyond_human_reach(KARL, "account_admin", can_reach) is False
    assert beyond_human_reach(OLLIE, "member", can_reach) is False


def test_build_roster_carries_scope_and_manager_visibility():
    """The annotation is only as good as the data reaching it. If build_roster
    dropped these fields, every consult would be stamped as a crossing (the
    Teammate defaults are company/not-visible) and the flag would stop meaning
    anything."""
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


# --- the consult path ---------------------------------------------------------

def test_a_member_may_consult_a_peer_they_could_not_open_themselves(
    client, monkeypatch, audited
):
    """The whole point. A member cannot open karl-m directly — the dashboard
    hides him and a session read 403s — but asking billie still works, because
    the user should not have to know that karl is the one to ask."""
    _as_tier(monkeypatch, "member")
    _grants(monkeypatch)

    body = _consult(client)

    assert body["ok"] is True
    assert body["answer"] == "about 18 points"


def test_that_consult_is_stamped_as_beyond_the_humans_reach(
    client, monkeypatch, audited
):
    """Not blocked, but not invisible either. Removing the `crossed` argument
    in src/api/dispatch.py makes this fail."""
    _as_tier(monkeypatch, "member")
    _grants(monkeypatch)

    _consult(client)

    assert audited[-1]["beyond_human_reach"] is True


def test_an_in_reach_consult_is_not_stamped(client, monkeypatch, audited):
    """The other half — a flag that were always True would carry no signal."""
    _as_tier(monkeypatch, "account_admin")
    _grants(monkeypatch)

    _consult(client)

    assert audited[-1]["beyond_human_reach"] is False


def test_a_user_scoped_peer_is_never_a_crossing(client, monkeypatch, audited):
    _as_tier(monkeypatch, "member")
    _grants(monkeypatch, "sure")

    assert _consult(client, to_agent="default")["ok"] is True
    assert audited[-1]["beyond_human_reach"] is False


def test_an_unknown_peer_is_still_refused(client, monkeypatch):
    """Dropping the reachability filter must not drop the roster check itself.
    An agent that is not on the bench is still refused."""
    _as_tier(monkeypatch, "member")

    assert _consult(client, to_agent="no-such-agent")["reason"] == "unknown_peer"


def test_a_refusal_is_stamped_too(client, monkeypatch, audited):
    """The flag is computed from the roster, before the outcome is known, so a
    refused consult to an out-of-reach peer is recorded the same way. An
    attempt is as interesting as a success when answering the question later."""
    _as_tier(monkeypatch, "member")
    monkeypatch.setattr("src.api.dispatch.port_for", lambda agent, entries: None)

    body = _consult(client)

    assert body["ok"] is False
    assert audited[-1]["beyond_human_reach"] is True


# --- the listing --------------------------------------------------------------

def test_list_teammates_shows_the_whole_bench_regardless_of_tier(
    client, monkeypatch
):
    """The calling agent sees every peer. Hiding one would put the user back in
    the position of having to know who to ask."""
    _as_tier(monkeypatch, "member")

    body = client.get("/v1/dispatch/teammates?agent=billie&session_id=s1",
                      headers=AUTH).json()

    assert sorted(t["agent_id"] for t in body["teammates"]) == ["default", "karl-m"]


def test_the_listing_is_identical_at_every_tier(client, monkeypatch):
    """A tier-dependent roster is exactly what was removed; this fails if any
    of it comes back."""
    seen = []
    for tier in ("member", "manager", "account_admin", "platform_operator"):
        _as_tier(monkeypatch, tier)
        body = client.get("/v1/dispatch/teammates?agent=billie&session_id=s1",
                          headers=AUTH).json()
        seen.append(sorted(t["agent_id"] for t in body["teammates"]))

    assert seen == [["default", "karl-m"]] * 4


def test_the_listing_still_requires_resolvable_provenance(client, monkeypatch):
    """Unfiltered is not unauthenticated. This endpoint still refuses a caller
    whose human cannot be resolved — otherwise any holder of the shared
    ORCHESTRATOR_KEY, which is every profile on the box, could enumerate the
    bench."""
    _as_tier(monkeypatch, "member")
    monkeypatch.setattr("src.api.dispatch.get_session_owner", lambda a, s: None)

    body = client.get("/v1/dispatch/teammates?agent=billie&session_id=s1",
                      headers=AUTH).json()

    assert body.get("teammates", []) == []
