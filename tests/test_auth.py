import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from src.auth import require_bearer, AuthError
from src.config import Config
from pathlib import Path


@pytest.fixture
def app(monkeypatch, tmp_path):
    cfg = Config(
        orchestrator_key="topsecret",
        hermes_stack_dir=tmp_path / "stack",
        hermes_home=tmp_path / ".hermes",
        hermes_profiles_dir=tmp_path / "profiles",
        systemd_user_dir=tmp_path / "systemd",
        audit_log_path=tmp_path / "audit.log",
        instance_id="default",
    )
    a = FastAPI()
    a.state.config = cfg

    @a.get("/protected")
    def protected(_: None = Depends(require_bearer)) -> dict:
        return {"ok": True}

    return a


def test_missing_header_returns_401(app):
    c = TestClient(app)
    assert c.get("/protected").status_code == 401


def test_wrong_token_returns_401(app):
    c = TestClient(app)
    assert c.get("/protected", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_valid_token_returns_200(app):
    c = TestClient(app)
    r = c.get("/protected", headers={"Authorization": "Bearer topsecret"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_malformed_header_returns_401(app):
    c = TestClient(app)
    assert c.get("/protected", headers={"Authorization": "topsecret"}).status_code == 401
