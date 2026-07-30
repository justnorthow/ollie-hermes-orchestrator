"""Server-side recursion bound for dispatch.

Why this exists instead of a propagated chain
---------------------------------------------
`ConsultRequest.chain` is agent-controlled data. The plugin builds a fresh
payload on the peer's box, and nothing carries a chain across the gateway hop,
so `req.chain` is empty on every real call and authority.check()'s cycle and
hop tests can never fire in production. Making the plugin propagate a chain
would not fix it either: a chain asserted by the calling process is exactly as
trustworthy as an identity asserted by the calling process, which the rest of
this design already refuses to accept.

So the bound lives here, in the one process every consult must pass through.

What it stops
-------------
billie consults karl-m; karl-m's model turn calls `ask_teammate("billie", ...)`;
billie's turn calls karl-m again. Each level is a nested synchronous completion
holding an httpx connection and a gateway generation slot, and each level is
paid model output. Two guards:

* **Per (human, peer).** While a consult to karl-m is open for user U, a second
  consult to karl-m for U is refused. A ping-pong between two agents has to
  re-enter one of the two peers to keep going, so this cuts it at depth two.
* **Process-wide depth.** A hard ceiling on concurrently open consults, in case
  a cascade finds a path the pairwise guard does not cover (a long ring of
  distinct agents, or provenance resolving to different humans partway down).

Concurrency
-----------
The orchestrator is one process serving concurrent requests. The dispatch
endpoints are plain `def`, so FastAPI runs them in Starlette's worker
threadpool: two consults really are two OS threads in this object at once. All
mutation is therefore done under a `threading.Lock`, held only for the O(1)
set/counter updates and never across the network call. The structure is also
correct if the endpoints are ever converted to `async def` -- there is no
`await` inside the critical section, so the lock can never be held across a
suspension point.

Releases go through `try/finally`. A leaked entry would block that
(human, peer) pair until the process restarts, which is a worse failure than
the recursion it guards against.
"""
import logging
import threading
from contextlib import contextmanager

_logger = logging.getLogger(__name__)


class InFlight:
    """Tracks open consults. One instance per process; see src/api/dispatch.py."""

    def __init__(self, max_depth: int):
        self._lock = threading.Lock()
        self._open: set[tuple[str, str]] = set()
        self._max_depth = max_depth

    @contextmanager
    def hold(self, user_id: str, to_agent: str):
        """Context manager yielding None when admitted, else a refusal detail.

        Usage is deliberately shaped so the caller cannot forget to release:

            with inflight.hold(origin.user_id, req.to_agent) as denial:
                if denial is not None:
                    return refuse(denial)
                ...

        The yielded value is a *detail string*, not a reason: the reason is
        always cap_exceeded, and it is the API layer that owns building
        ConsultResults.
        """
        key = (user_id, to_agent)
        denial = self._acquire(key)
        if denial is not None:
            _logger.warning("dispatch: in-flight guard refused %s -> %s: %s",
                            user_id, to_agent, denial)
            yield denial
            return
        try:
            yield None
        finally:
            self._release(key)

    def _acquire(self, key: tuple[str, str]) -> str | None:
        with self._lock:
            if key in self._open:
                return (f"{key[1]} is already answering an open consult for this "
                        f"human — a consult chain cannot re-enter a peer it is "
                        f"already inside")
            if len(self._open) >= self._max_depth:
                return (f"the in-flight consult limit of {self._max_depth} for "
                        f"this instance is already reached")
            self._open.add(key)
            return None

    def _release(self, key: tuple[str, str]) -> None:
        with self._lock:
            self._open.discard(key)

    def depth(self) -> int:
        """Currently open consults. For tests and diagnostics."""
        with self._lock:
            return len(self._open)
