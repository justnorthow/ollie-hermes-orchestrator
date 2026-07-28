"""Robustness tests for atomic store writes, corrupt-file guards, Folder
validators, and SSO endpoint headers / error codes."""
import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.apps import router as apps_router
from src.api.sso import router as sso_router
from src.auth import require_bearer
from src.config import Config
from src.folders_store import read_folders, write_folders
from src.models import Folder


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _cfg(tmp_path):
    return Config(
        orchestrator_key="test-key",
        hermes_stack_dir=tmp_path,
        hermes_home=tmp_path / ".hermes",
        hermes_profiles_dir=tmp_path / ".hermes" / "profiles",
        systemd_user_dir=tmp_path / ".config" / "systemd" / "user",
        audit_log_path=tmp_path / "audit.log",
        instance_id="default",
        orch_env_path=tmp_path / "orch.env",
    )


# ---------------------------------------------------------------------------
# Fix 1+2: folders_store atomic write-then-read round-trip (incl. UTF-8 chars)
# ---------------------------------------------------------------------------

def test_folders_store_roundtrip_unicode(tmp_path):
    """write_folders + read_folders survive emoji and accented characters."""
    cfg = _cfg(tmp_path)
    folders = [{"id": "café", "name": "☕ Café", "order": 0, "appIds": []}]
    write_folders(cfg, folders)
    result = read_folders(cfg)
    assert result == folders


def test_folders_store_atomic_creates_parent(tmp_path):
    """write_folders creates hermes_home if it doesn't exist yet."""
    cfg = _cfg(tmp_path)
    assert not cfg.hermes_home.exists()
    write_folders(cfg, [{"id": "x", "name": "X", "order": 0, "appIds": []}])
    assert (cfg.hermes_home / "folders.json").exists()


# ---------------------------------------------------------------------------
# Fix 1+2+3: apps store atomic write-then-read round-trip + corrupt-file guard
# ---------------------------------------------------------------------------

def _apps_cfg_and_client(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        'AGENTS_JSON=[{"id":"test-agent","name":"Test",'
        '"gatewayUrl":"http://host.docker.internal:9110",'
        '"dashboardUrl":"http://host.docker.internal:9111","color":"#7c3aed"}]'
    )
    cfg = _cfg(tmp_path)
    app = FastAPI()
    app.state.config = cfg
    app.dependency_overrides[require_bearer] = lambda: None
    app.include_router(apps_router)
    return cfg, TestClient(app)


def test_apps_write_then_read_roundtrip(tmp_path):
    """Register an app and read it back — exercises atomic write path."""
    _cfg_obj, client = _apps_cfg_and_client(tmp_path)
    payload = {
        "id": "my-app",
        "label": "My App",
        "icon": "",
        "description": "desc",
        "componentType": "Widget",
    }
    r = client.post("/v1/agents/test-agent/apps", json=payload)
    assert r.status_code == 201
    r2 = client.get("/v1/agents/test-agent/apps")
    assert r2.status_code == 200
    apps = r2.json()["apps"]
    assert len(apps) == 1
    assert apps[0]["id"] == "my-app"


def test_read_apps_returns_empty_for_non_list_file(tmp_path):
    """_read_apps must return [] when apps.json contains a dict, not a list."""
    cfg_obj, client = _apps_cfg_and_client(tmp_path)
    # Manually write a corrupt (non-list) apps.json
    agent_dir = cfg_obj.hermes_profiles_dir / "test-agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "apps.json").write_text(json.dumps({"broken": True}), encoding="utf-8")

    r = client.get("/v1/agents/test-agent/apps")
    assert r.status_code == 200
    assert r.json() == {"apps": []}


def test_apps_write_roundtrip_unicode(tmp_path):
    """App labels with emoji survive the UTF-8 write/read cycle."""
    _cfg_obj, client = _apps_cfg_and_client(tmp_path)
    payload = {
        "id": "emoji-app",
        "label": "Star ⭐ App",
        "icon": "",
        "description": "",
        "componentType": "EmojiWidget",
    }
    client.post("/v1/agents/test-agent/apps", json=payload)
    apps = client.get("/v1/agents/test-agent/apps").json()["apps"]
    assert apps[0]["label"] == "Star ⭐ App"


# ---------------------------------------------------------------------------
# Fix 4: Folder validators
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_id", ["", "  ", "\t"])
def test_folder_blank_id_rejected(bad_id):
    with pytest.raises(Exception):
        Folder(id=bad_id, name="Valid Name")


@pytest.mark.parametrize("bad_name", ["", "  ", "\t"])
def test_folder_blank_name_rejected(bad_name):
    with pytest.raises(Exception):
        Folder(id="valid-id", name=bad_name)


def test_folder_valid_accepts():
    f = Folder(id="my-folder", name="My Folder")
    assert f.id == "my-folder"
    assert f.name == "My Folder"


# ---------------------------------------------------------------------------
# Fix 6: SSO endpoint — Cache-Control header + 503 when unconfigured + 401
# ---------------------------------------------------------------------------

@pytest.fixture()
def sso_client(tmp_path):
    cfg = _cfg(tmp_path)
    app = FastAPI()
    app.state.config = cfg
    app.include_router(sso_router)
    return TestClient(app, raise_server_exceptions=False)


def test_sso_503_when_secret_unset(sso_client, monkeypatch):
    """GET /v1/sso/hia-token returns 503 when HIA_SSO_SECRET is missing."""
    monkeypatch.delenv("HIA_SSO_SECRET", raising=False)
    monkeypatch.delenv("HIA_BROKER_EMAIL", raising=False)
    r = sso_client.get(
        "/v1/sso/hia-token",
        headers={"Authorization": "Bearer test-key"},
    )
    assert r.status_code == 503


def test_sso_401_without_bearer(sso_client, monkeypatch):
    """GET /v1/sso/hia-token returns 401 without a valid bearer token."""
    monkeypatch.setenv("HIA_SSO_SECRET", "s3cr3t")
    monkeypatch.setenv("HIA_BROKER_EMAIL", "bot@example.com")
    r = sso_client.get("/v1/sso/hia-token")
    assert r.status_code == 401


def test_sso_no_store_cache_control(sso_client, monkeypatch):
    """GET /v1/sso/hia-token returns Cache-Control: no-store on success."""
    monkeypatch.setenv("HIA_SSO_SECRET", "s3cr3t")
    monkeypatch.setenv("HIA_BROKER_EMAIL", "bot@example.com")
    r = sso_client.get(
        "/v1/sso/hia-token",
        headers={"Authorization": "Bearer test-key"},
    )
    assert r.status_code == 200
    assert "no-store" in r.headers.get("cache-control", "").lower()
