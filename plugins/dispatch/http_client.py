"""Thin client for the orchestrator's dispatch API. The only I/O in the plugin."""
import os

import httpx

# Must exceed the orchestrator's WORST CASE for one consult, not just the
# gateway leg. Server-side a cold request can spend, in series:
#   10s  owner lookup      (get_session_owner, src/api/sessions.py)
# + 10s  tier lookup       (resolve_tier, src/api/roles.py)
# + 30s  peer generation   (_GATEWAY_TIMEOUT, src/api/dispatch.py)
# + 10s  audit write       (_AUDIT_TIMEOUT, src/api/dispatch.py)
# = 60s. The old 35.0 accounted for the gateway leg alone, so on a cold request
# this client gave up while the server was still working: the server then went
# on to complete the consult and write a governance_events row with
# status="ok", while the calling model was told the orchestrator was
# unreachable. An audit trail recording a granted consult that nobody received
# is worse than a slow one. 75s leaves headroom over the 60s total.
# tests/test_dispatch_timeout_budget.py asserts this against the server's own
# constants so the two cannot drift apart silently.
_TIMEOUT = 75.0


class DispatchHttpClient:
    def __init__(self, base_url: str | None = None, key: str | None = None):
        self._base = (base_url or os.environ.get("ORCHESTRATOR_URL")
                      or "http://127.0.0.1:9123").rstrip("/")
        self._key = key or os.environ.get("ORCHESTRATOR_KEY", "")

    def post(self, path: str, payload: dict) -> dict:
        resp = httpx.post(
            f"{self._base}{path}",
            headers={"Authorization": f"Bearer {self._key}",
                     "Content-Type": "application/json"},
            json=payload,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def get(self, path: str, params: dict) -> dict:
        resp = httpx.get(
            f"{self._base}{path}",
            headers={"Authorization": f"Bearer {self._key}"},
            params=params,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
