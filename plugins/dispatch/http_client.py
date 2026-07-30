"""Thin client for the orchestrator's dispatch API. The only I/O in the plugin."""
import os

import httpx

_TIMEOUT = 35.0  # must exceed the orchestrator's own 30s gateway timeout


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
