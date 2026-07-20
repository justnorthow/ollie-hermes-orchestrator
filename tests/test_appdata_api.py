import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.appdata import router
from src.auth import require_bearer
from src.config import Config


@pytest.fixture()
def cfg(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        'AGENTS_JSON=[{"id":"prospecting-expert","name":"Head of Outbound",'
        '"gatewayUrl":"http://host.docker.internal:9110",'
        '"dashboardUrl":"http://host.docker.internal:9111","color":"#00D0A8"}]'
    )
    return Config(
        orchestrator_key="test-key",
        hermes_stack_dir=tmp_path,
        hermes_home=tmp_path / ".hermes",
        hermes_profiles_dir=tmp_path / ".hermes" / "profiles",
        systemd_user_dir=tmp_path / ".config" / "systemd" / "user",
        audit_log_path=tmp_path / "audit.log",
        instance_id="default",
    )


@pytest.fixture()
def client(cfg):
    app = FastAPI()
    app.state.config = cfg
    app.dependency_overrides[require_bearer] = lambda: None
    app.include_router(router)
    return TestClient(app)


def seed(cfg, key: str, doc: dict) -> None:
    path = cfg.hermes_profiles_dir / "prospecting-expert" / "workspace" / "appdata" / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_get_appdata_returns_document(client, cfg):
    seed(cfg, "prospecting/current-run", {"version": 1, "segment": "medical"})
    r = client.get("/v1/agents/prospecting-expert/appdata/prospecting/current-run")
    assert r.status_code == 200
    assert r.json() == {"version": 1, "segment": "medical"}


def test_get_appdata_missing_file_404(client):
    r = client.get("/v1/agents/prospecting-expert/appdata/prospecting/current-run")
    assert r.status_code == 404


def test_get_appdata_unknown_agent_404(client):
    r = client.get("/v1/agents/no-such-agent/appdata/prospecting/current-run")
    assert r.status_code == 404


def test_get_appdata_traversal_key_400(client, cfg):
    seed(cfg, "prospecting/current-run", {"version": 1})
    # ".." and uppercase/illegal chars are rejected before touching the fs
    # Use URL-encoded %2E%2E to prevent TestClient from normalizing the path
    assert client.get("/v1/agents/prospecting-expert/appdata/%2E%2E/secrets").status_code == 400
    assert client.get("/v1/agents/prospecting-expert/appdata/Bad_Key").status_code == 400


def read_seeded(cfg, key: str) -> dict:
    path = cfg.hermes_profiles_dir / "prospecting-expert" / "workspace" / "appdata" / f"{key}.json"
    return json.loads(path.read_text(encoding="utf-8"))


CANDS = {
    "version": 3,
    "status": "in_review",
    "candidates": [
        {"id": "a1", "bucket": "grey", "decision": "pending"},
        {"id": "a2", "bucket": "green"},
    ],
}


def test_patch_appdata_applies_pointer_updates_and_bumps_version(client, cfg):
    seed(cfg, "prospecting/candidates", CANDS)
    r = client.patch(
        "/v1/agents/prospecting-expert/appdata/prospecting/candidates",
        json={"baseVersion": 3, "updates": [
            {"pointer": "/candidates/0/decision", "value": "keep"},
            {"pointer": "/status", "value": "final"},
        ]},
    )
    assert r.status_code == 200
    assert r.json() == {"version": 4}
    doc = read_seeded(cfg, "prospecting/candidates")
    assert doc["candidates"][0]["decision"] == "keep"
    assert doc["status"] == "final"
    assert doc["version"] == 4


def test_patch_appdata_stale_version_409(client, cfg):
    seed(cfg, "prospecting/candidates", CANDS)
    r = client.patch(
        "/v1/agents/prospecting-expert/appdata/prospecting/candidates",
        json={"baseVersion": 2, "updates": [{"pointer": "/status", "value": "final"}]},
    )
    assert r.status_code == 409
    assert read_seeded(cfg, "prospecting/candidates")["version"] == 3  # untouched


def test_patch_appdata_bad_pointer_400_leaves_file_untouched(client, cfg):
    seed(cfg, "prospecting/candidates", CANDS)
    r = client.patch(
        "/v1/agents/prospecting-expert/appdata/prospecting/candidates",
        json={"baseVersion": 3, "updates": [{"pointer": "/candidates/99/decision", "value": "keep"}]},
    )
    assert r.status_code == 400
    assert read_seeded(cfg, "prospecting/candidates") == CANDS


def test_patch_appdata_missing_file_404(client):
    r = client.patch(
        "/v1/agents/prospecting-expert/appdata/prospecting/candidates",
        json={"baseVersion": 1, "updates": [{"pointer": "/status", "value": "final"}]},
    )
    assert r.status_code == 404
