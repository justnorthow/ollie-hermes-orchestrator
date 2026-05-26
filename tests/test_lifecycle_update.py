import pytest
from src.lifecycle import create_agent, update_agent, CreateRequest, UpdateRequest


@pytest.mark.asyncio
async def test_update_changes_display_name_without_restart(fake_env):
    base = CreateRequest(
        name="paige", display_name="Paige", color="#abc", provider="anthropic",
        model="m", api_key="k", system_prompt=None, enabled_skills=[],
        api_server_key="shared",
    )
    [ev async for ev in create_agent(base)]

    result = await update_agent("paige", UpdateRequest(displayName="P", restart=False))
    assert result["ok"] is True
    assert result["restarted"] is False

    from src.agents_json import read_agents
    entries = read_agents(fake_env["stack"] / ".env")
    paige = next(e for e in entries if e.id == "paige")
    assert paige.name == "P"


@pytest.mark.asyncio
async def test_update_model_bounces_gateway(fake_env):
    base = CreateRequest(
        name="paige", display_name="Paige", color="#abc", provider="anthropic",
        model="m1", api_key="k", system_prompt=None, enabled_skills=[],
        api_server_key="shared",
    )
    [ev async for ev in create_agent(base)]
    result = await update_agent("paige", UpdateRequest(model="m2"))
    assert result["ok"] is True
    assert result["restarted"] is True
    log = (fake_env["logs"] / "systemctl.log").read_text()
    assert "restart hermes-gateway-paige" in log
