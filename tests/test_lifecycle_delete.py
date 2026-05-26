import pytest
from src.lifecycle import create_agent, delete_agent, CreateRequest


async def _create(name: str):
    req = CreateRequest(
        name=name, display_name=None, color=None, provider="anthropic",
        model="m", api_key="k", system_prompt=None, enabled_skills=[],
        api_server_key="shared",
    )
    return [ev async for ev in create_agent(req)]


@pytest.mark.asyncio
async def test_delete_agent_removes_all_artifacts(fake_env):
    await _create("paige")
    result = await delete_agent("paige")
    assert result["ok"] is True
    # filesystem state
    assert not (fake_env["profiles"] / "paige").exists()
    # AGENTS_JSON cleaned
    from src.agents_json import read_agents
    assert all(e.id != "paige" for e in read_agents(fake_env["stack"] / ".env"))
    # systemd units gone
    assert not (fake_env["systemd"] / "hermes-dashboard-paige.service").exists()


@pytest.mark.asyncio
async def test_delete_unknown_agent_returns_not_found(fake_env):
    result = await delete_agent("ghost")
    assert result["ok"] is False
    assert result["error"] == "not_found"


@pytest.mark.asyncio
async def test_delete_refuses_default(fake_env):
    result = await delete_agent("default")
    assert result["ok"] is False
    assert "reserved" in result["error"].lower() or "default" in result["error"].lower()
