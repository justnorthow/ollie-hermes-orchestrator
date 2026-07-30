"""The recursion bound, and the release discipline that keeps it from wedging.

`req.chain` is empty on every real call — the plugin hardcodes `chain: []` and
nothing carries a chain across the gateway hop — so authority.check()'s cycle
and hop tests are dead code in production. Two `fast`, `direct` agents can
therefore consult each other without limit, each level a nested synchronous
completion holding a connection and a paid generation slot, unwinding only when
timeouts fire. This module pins the server-side bound that actually stops it.
"""
import threading

import pytest

from src.dispatch.inflight import InFlight


def test_a_second_consult_to_the_same_peer_for_the_same_human_is_refused():
    reg = InFlight(max_depth=10)

    with reg.hold("u-1", "karl-m") as first:
        assert first is None
        with reg.hold("u-1", "karl-m") as second:
            assert second is not None
            assert "karl-m" in second


def test_the_pair_is_free_again_once_the_first_consult_finishes():
    reg = InFlight(max_depth=10)

    with reg.hold("u-1", "karl-m") as denial:
        assert denial is None
    with reg.hold("u-1", "karl-m") as denial:
        assert denial is None

    assert reg.depth() == 0


def test_a_different_human_is_not_blocked_by_someone_elses_consult():
    """The key is (human, peer). Two people asking karl-m at once is normal use,
    not a loop — keying on the peer alone would make dispatch single-user."""
    reg = InFlight(max_depth=10)

    with reg.hold("u-1", "karl-m") as first:
        with reg.hold("u-2", "karl-m") as second:
            assert first is None and second is None


def test_a_different_peer_is_not_blocked():
    reg = InFlight(max_depth=10)

    with reg.hold("u-1", "karl-m"):
        with reg.hold("u-1", "deep") as denial:
            assert denial is None


def test_mutual_recursion_is_cut_when_it_re_enters_a_peer():
    """billie -> karl -> billie -> karl. The second karl is the same
    (human, peer) pair as the first and is refused, so the cascade stops."""
    reg = InFlight(max_depth=10)

    with reg.hold("u-1", "karl-m") as a:
        with reg.hold("u-1", "billie") as b:
            with reg.hold("u-1", "karl-m") as c:
                assert a is None and b is None
                assert c is not None


def test_the_depth_ceiling_refuses_past_the_bound():
    """The pairwise guard does not cover a ring of distinct agents, or a chain
    whose provenance resolves to different humans partway down. The depth
    counter is the backstop for both."""
    reg = InFlight(max_depth=2)

    with reg.hold("u-1", "a") as first:
        with reg.hold("u-2", "b") as second:
            with reg.hold("u-3", "c") as third:
                assert first is None and second is None
                assert third is not None
                assert "2" in third


def test_the_bound_comes_from_caps_hop_cap_not_a_new_setting():
    from src.api.dispatch import _CAPS, _INFLIGHT
    from src.dispatch.authority import Caps

    assert _CAPS.hop_cap == Caps().hop_cap
    assert _INFLIGHT._max_depth == Caps().hop_cap


# --- release discipline ------------------------------------------------------

def test_an_exception_inside_the_hold_still_releases_it():
    """A leaked entry blocks that (human, peer) pair until the process
    restarts — a worse bug than the recursion it guards against. Removing the
    try/finally in InFlight.hold makes this test fail."""
    reg = InFlight(max_depth=10)

    with pytest.raises(RuntimeError):
        with reg.hold("u-1", "karl-m"):
            raise RuntimeError("peer exploded")

    assert reg.depth() == 0
    with reg.hold("u-1", "karl-m") as denial:
        assert denial is None


def test_a_timeout_inside_the_hold_still_releases_it():
    reg = InFlight(max_depth=10)

    with pytest.raises(TimeoutError):
        with reg.hold("u-1", "karl-m"):
            raise TimeoutError("gateway timed out")

    assert reg.depth() == 0


def test_a_refused_hold_does_not_consume_a_slot():
    """A refusal must not decrement someone else's budget on release, nor add
    an entry it never acquired."""
    reg = InFlight(max_depth=10)

    with reg.hold("u-1", "karl-m"):
        with reg.hold("u-1", "karl-m") as denial:
            assert denial is not None
        # The refused inner hold must not have removed the outer one's entry.
        assert reg.depth() == 1

    assert reg.depth() == 0


def test_concurrent_holds_from_real_threads_do_not_corrupt_the_counter():
    """FastAPI runs these `def` endpoints in Starlette's worker threadpool, so
    two consults really are two OS threads inside this object."""
    reg = InFlight(max_depth=1000)
    start = threading.Barrier(8)
    errors = []

    def worker(n):
        try:
            start.wait(timeout=5)
            for i in range(50):
                with reg.hold(f"u-{n}", f"peer-{i}") as denial:
                    assert denial is None
        except Exception as exc:  # noqa: BLE001 — surfaced via `errors`
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert errors == []
    assert reg.depth() == 0
