import json

import pytest

from src.lifecycle import CreateRequest, create_agent, delete_agent


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


def _req(name="mail-agent"):
    return CreateRequest(
        name=name, display_name="Karl M", color=None, provider="openai",
        model="gpt-5.6-sol", api_key="k", system_prompt=None,
        enabled_skills=[], api_server_key="gk", auth_method="inherit",
    )


async def test_create_adds_the_agent_to_the_proxy_maps(fake_env, orch_env):
    events = [ev async for ev in create_agent(_req())]
    assert events[-1]["event"] == "done", events[-1]
    assert "mail-agent" in _read_key(orch_env, GW)


async def test_delete_removes_the_agent_from_the_proxy_maps(fake_env, orch_env):
    events = [ev async for ev in create_agent(_req())]
    assert events[-1]["event"] == "done", events[-1]
    assert (await delete_agent("mail-agent"))["ok"] is True
    assert "mail-agent" not in _read_key(orch_env, GW)


async def test_create_still_succeeds_when_map_sync_fails(
        fake_env, orch_env, monkeypatch):
    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr("src.lifecycle.proxy_maps.sync", boom)
    events = [ev async for ev in create_agent(_req())]
    assert events[-1]["event"] == "done", events[-1]
