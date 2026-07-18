import pytest
from src.agents_json import AgentEntry, read_agents, write_agent
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


@pytest.mark.asyncio
async def test_update_model_writes_model_default_config_key(fake_env):
    """The model must be written to `model.default` (what create uses and what
    the Hermes config schema reads), not the top-level `model` key — otherwise
    the gateway keeps the old model while the UI shows the new one."""
    base = CreateRequest(
        name="paige", display_name="Paige", color="#abc", provider="anthropic",
        model="m1", api_key="k", system_prompt=None, enabled_skills=[],
        api_server_key="shared",
    )
    [ev async for ev in create_agent(base)]
    (fake_env["logs"] / "hermes.log").write_text("")  # ignore create-time writes

    await update_agent("paige", UpdateRequest(model="m2", restart=False))

    log = (fake_env["logs"] / "hermes.log").read_text()
    assert "config set model.default m2" in log
    assert "config set model m2" not in log


@pytest.mark.asyncio
async def test_update_apikey_preserves_server_key_and_provider(fake_env):
    """Updating the provider key must (a) preserve the existing shared
    API_SERVER_KEY rather than clobbering it with the literal "shared", and
    (b) write the key under the profile's ACTUAL provider var, not a hardcoded
    ANTHROPIC_API_KEY."""
    base = CreateRequest(
        name="paige", display_name="Paige", color="#abc", provider="openai",
        model="m1", api_key="orig-key", system_prompt=None, enabled_skills=[],
        api_server_key="realsecret",
    )
    [ev async for ev in create_agent(base)]
    profile_env = fake_env["profiles"] / "paige" / ".env"
    assert "API_SERVER_KEY=realsecret" in profile_env.read_text()
    assert "OPENAI_API_KEY=orig-key" in profile_env.read_text()

    await update_agent("paige", UpdateRequest(apiKey="new-key", restart=False))

    text = profile_env.read_text()
    assert "API_SERVER_KEY=realsecret" in text
    assert "API_SERVER_KEY=shared" not in text
    assert "OPENAI_API_KEY=new-key" in text
    assert "ANTHROPIC_API_KEY" not in text


@pytest.mark.asyncio
async def test_update_agent_clears_avatar_on_empty(fake_env):
    base = CreateRequest(
        name="paige", display_name="Paige", color="#abc", provider="anthropic",
        model="m", api_key="k", system_prompt=None, enabled_skills=[],
        api_server_key="shared",
    )
    [ev async for ev in create_agent(base)]

    await update_agent("paige", UpdateRequest(avatar_url="https://x/a.jpg", restart=False))
    from src.agents_json import read_agents
    entry = next(e for e in read_agents(fake_env["stack"] / ".env") if e.id == "paige")
    assert entry.avatar_url == "https://x/a.jpg"

    await update_agent("paige", UpdateRequest(avatar_url="", restart=False))
    entry = next(e for e in read_agents(fake_env["stack"] / ".env") if e.id == "paige")
    assert entry.avatar_url is None


@pytest.mark.asyncio
async def test_update_sets_and_clears_voice(fake_env):
    env_path = fake_env["stack"] / ".env"
    write_agent(env_path, AgentEntry(
        id="marketing-agent", name="Olivia", gateway_port=8643,
        dashboard_port=9121, color="#888888",
    ))
    r = await update_agent("marketing-agent", UpdateRequest(voice="en-US-EmmaNeural", restart=False))
    assert r["ok"]
    assert read_agents(env_path)[0].voice == "en-US-EmmaNeural"
    r = await update_agent("marketing-agent", UpdateRequest(voice="", restart=False))
    assert r["ok"]
    assert read_agents(env_path)[0].voice is None


@pytest.mark.asyncio
async def test_update_preserves_scope_and_manager_visible(fake_env):
    env_path = fake_env["stack"] / ".env"
    write_agent(env_path, AgentEntry(
        id="default", name="Ollie", gateway_port=8642, dashboard_port=9119,
        color="#888888", scope="user", manager_visible=True, voice="en-GB-RyanNeural",
    ))
    r = await update_agent("default", UpdateRequest(displayName="Ollie2", restart=False))
    assert r["ok"]
    e = read_agents(env_path)[0]
    assert e.scope == "user"
    assert e.manager_visible is True
    assert e.voice == "en-GB-RyanNeural"   # untouched update preserves voice too
