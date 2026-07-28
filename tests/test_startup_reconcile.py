import json

import pytest

from src.api.main import create_app


GW = "HERMES_GATEWAY_URLS"


def _read_key(path, key):
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            return json.loads(line[len(key) + 1:])
    return None


@pytest.fixture
def orch_env(monkeypatch, tmp_path):
    p = tmp_path / "orch.env"
    p.write_text("ORCHESTRATOR_KEY=x\n", encoding="utf-8")
    monkeypatch.setenv("ORCH_ENV", str(p))
    return p


def test_startup_backfills_an_agent_missing_from_the_maps(fake_env, orch_env):
    stack_env = fake_env["stack"] / ".env"
    stack_env.write_text(
        "HERMES_GATEWAY_KEY=k\n"
        'AGENTS_JSON=[{"id":"default","name":"Billie",'
        '"gatewayUrl":"http://host.docker.internal:8642",'
        '"dashboardUrl":"http://host.docker.internal:9119","color":"#888888"}]\n',
        encoding="utf-8")
    create_app()
    assert _read_key(orch_env, GW) == {"default": "http://127.0.0.1:8642"}


def test_startup_survives_a_map_sync_failure(fake_env, orch_env, monkeypatch):
    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr("src.api.main.proxy_maps.sync", boom)
    assert create_app() is not None
