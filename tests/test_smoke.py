from fastapi.testclient import TestClient
import pytest

from src.api import main
from src.api.main import app


def test_healthz_returns_ok():
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_build_raises_when_production_app_creation_fails(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_KEY", "secret")

    def boom():
        raise RuntimeError("bad config")

    monkeypatch.setattr(main, "create_app", boom)
    with pytest.raises(RuntimeError, match="bad config"):
        main._build_or_placeholder()
