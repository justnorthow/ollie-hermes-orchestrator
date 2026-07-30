"""The plugin's client budget must exceed the orchestrator's worst case.

The old comment on `_TIMEOUT` — "must exceed the orchestrator's own 30s gateway
timeout" — accounted for one leg. A cold request also spends up to 10s in
get_session_owner, up to 10s in resolve_tier, and up to 10s in the audit write:
about 60s. When the client's 35s fired first, the server carried on, completed
the consult, and wrote a dispatch_consult row with status="ok" while the calling
model was told the orchestrator was unreachable. An audit trail that records a
granted consult nobody received is worse than a slow one.

This test is the only thing keeping the two numbers — which live in two
separately deployed processes — from drifting apart again.
"""
from plugins.dispatch.http_client import _TIMEOUT as CLIENT_TIMEOUT
from src.api.dispatch import (
    SERVER_WORST_CASE_SECONDS,
    _AUDIT_TIMEOUT,
    _GATEWAY_TIMEOUT,
    _OWNER_LOOKUP_TIMEOUT,
    _TIER_LOOKUP_TIMEOUT,
)


def test_the_client_budget_exceeds_the_servers_worst_case():
    assert CLIENT_TIMEOUT > SERVER_WORST_CASE_SECONDS


def test_the_worst_case_is_the_sum_of_every_leg_not_just_the_gateway():
    """Pins the mistake itself: a budget derived from the gateway leg alone."""
    assert SERVER_WORST_CASE_SECONDS == (
        _OWNER_LOOKUP_TIMEOUT + _TIER_LOOKUP_TIMEOUT
        + _GATEWAY_TIMEOUT + _AUDIT_TIMEOUT
    )
    assert SERVER_WORST_CASE_SECONDS > _GATEWAY_TIMEOUT


def test_the_mirrored_lookup_timeouts_match_their_owners():
    """_OWNER_LOOKUP_TIMEOUT and _TIER_LOOKUP_TIMEOUT mirror values owned by
    src/api/sessions.py and src/api/roles.py, which hardcode them inline at
    their httpx call sites and expose nothing to import. This asserts the
    literal each mirrors still appears in its owner, so a change there fails
    here rather than silently invalidating the budget."""
    import inspect

    from src.api import roles, sessions

    assert f"timeout={_OWNER_LOOKUP_TIMEOUT}" in inspect.getsource(
        sessions.get_session_owner)
    assert f"timeout={_TIER_LOOKUP_TIMEOUT}" in inspect.getsource(roles._fetch_tier)
