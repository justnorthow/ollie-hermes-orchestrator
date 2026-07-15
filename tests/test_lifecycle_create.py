import asyncio
import pytest
from src.lifecycle import create_agent, CreateRequest


@pytest.mark.asyncio
async def test_create_agent_happy_path_emits_all_steps(fake_env):
    req = CreateRequest(
        name="paige",
        display_name="Paige",
        color="#aabbcc",
        provider="anthropic",
        model="claude-sonnet-4.6",
        api_key="sk-x",
        system_prompt=None,
        enabled_skills=[],
        api_server_key="shared",
    )
    events = [ev async for ev in create_agent(req)]
    steps = [ev["step"] for ev in events if "step" in ev]
    assert "validate" in steps
    assert "allocate_ports" in steps
    assert "create_profile" in steps
    assert "write_profile_env" in steps
    assert "install_gateway" in steps
    assert "install_dashboard" in steps
    assert "update_agents_json" in steps
    assert "bounce_dashboard" in steps
    final = events[-1]
    assert final.get("event") == "done"
    assert final["agent"]["id"] == "paige"
    assert (fake_env["profiles"] / "paige").is_dir()


@pytest.mark.asyncio
async def test_concurrent_creates_both_complete(fake_env):
    """Two creates issued concurrently must serialize on the lock and both
    finish — the lock must not be acquired in a way that deadlocks the loop."""
    def _req(name):
        return CreateRequest(
            name=name, display_name=name, color=None, provider="anthropic",
            model="m", api_key="k", system_prompt=None, enabled_skills=[],
            api_server_key="shared",
        )

    async def run(name):
        return [ev async for ev in create_agent(_req(name))]

    a, b = await asyncio.wait_for(
        asyncio.gather(run("alpha"), run("beta")), timeout=15,
    )
    assert any(ev.get("event") == "done" for ev in a)
    assert any(ev.get("event") == "done" for ev in b)

    from src.agents_json import read_agents
    ids = {e.id for e in read_agents(fake_env["stack"] / ".env")}
    assert {"alpha", "beta"} <= ids


@pytest.mark.asyncio
async def test_create_agent_inherit_skips_provider_creds(fake_env):
    """auth_method='inherit' must NOT write any provider key into the profile .env.
    Hermes inherits whatever auth the host already has (OAuth, Codex, etc.)."""
    req = CreateRequest(
        name="codex-agent",
        display_name=None, color=None,
        provider="anthropic",
        model="",  # inherit path — no model specified by user
        api_key=None,
        system_prompt=None, enabled_skills=[],
        api_server_key="shared",
        auth_method="inherit",
    )
    events = [ev async for ev in create_agent(req)]
    assert events[-1].get("event") == "done"
    profile_env = (fake_env["profiles"] / "codex-agent" / ".env").read_text()
    assert "ANTHROPIC_API_KEY" not in profile_env
    assert "API_SERVER_PORT=" in profile_env  # API_SERVER_* still written


@pytest.mark.asyncio
async def test_create_agent_inherit_copies_model_config_from_default(fake_env):
    """auth_method='inherit' must copy model.default/provider/base_url from the
    default profile so Hermes has a provider configured for the new agent."""
    req = CreateRequest(
        name="codex-agent",
        display_name=None, color=None,
        provider="anthropic",
        model="",
        api_key=None,
        system_prompt=None, enabled_skills=[],
        api_server_key="shared",
        auth_method="inherit",
    )
    events = [ev async for ev in create_agent(req)]
    assert events[-1].get("event") == "done"
    # apply_config event should report which keys were inherited
    apply = next(ev for ev in events if ev.get("step") == "apply_config")
    assert "model.default" in apply.get("inherited", [])
    assert "model.provider" in apply.get("inherited", [])
    # the new profile's CLI shim should have received `config set model.default <value>`
    hermes_log = (fake_env["logs"] / "hermes.log").read_text()
    assert "config set model.default gpt-5.5" in hermes_log
    assert "config set model.provider openai-codex" in hermes_log
    # AGENTS_JSON entry shows the inherited model name (not "unknown")
    from src.agents_json import read_agents
    entry = next(e for e in read_agents(fake_env["stack"] / ".env") if e.id == "codex-agent")
    assert entry.model == "gpt-5.5"


@pytest.mark.asyncio
async def test_create_agent_subtitle_stripped_into_agents_json(fake_env):
    req = CreateRequest(
        name="olivia",
        display_name="Olivia",
        color="#7c3aed",
        provider="anthropic",
        model="claude-sonnet-4.6",
        api_key="sk-x",
        system_prompt=None,
        enabled_skills=[],
        api_server_key="shared",
        subtitle="  AI Head of Marketing  ",
    )
    events = [ev async for ev in create_agent(req)]
    final = events[-1]
    assert final.get("event") == "done"
    # SSE done-event carries the normalized subtitle too (same contract as GET)
    assert final["agent"]["subtitle"] == "AI Head of Marketing"
    from src.agents_json import read_agents
    entry = next(e for e in read_agents(fake_env["stack"] / ".env") if e.id == "olivia")
    assert entry.subtitle == "AI Head of Marketing"


@pytest.mark.asyncio
async def test_create_agent_without_subtitle_stores_none(fake_env):
    req = CreateRequest(
        name="paige",
        display_name="Paige",
        color="#aabbcc",
        provider="anthropic",
        model="claude-sonnet-4.6",
        api_key="sk-x",
        system_prompt=None,
        enabled_skills=[],
        api_server_key="shared",
        subtitle=None,
    )
    events = [ev async for ev in create_agent(req)]
    final = events[-1]
    assert final.get("event") == "done"
    assert final["agent"]["subtitle"] is None
    from src.agents_json import read_agents
    entry = next(e for e in read_agents(fake_env["stack"] / ".env") if e.id == "paige")
    assert entry.subtitle is None


@pytest.mark.asyncio
async def test_create_agent_rejects_duplicate_name(fake_env):
    base = dict(
        display_name="X", color=None, provider="anthropic", model="m",
        api_key="k", system_prompt=None, enabled_skills=[], api_server_key="s",
    )
    [ev async for ev in create_agent(CreateRequest(name="paige", **base))]
    events = [ev async for ev in create_agent(CreateRequest(name="paige", **base))]
    assert any(ev.get("event") == "error" for ev in events)


@pytest.mark.asyncio
async def test_create_agent_rolls_back_on_systemd_failure(fake_env, monkeypatch):
    from src import lifecycle

    def boom(*_a, **_kw):
        raise RuntimeError("systemd is sad")

    # patch the reference inside lifecycle (not systemd_ops) so the lifecycle
    # module's import binding is replaced
    monkeypatch.setattr(lifecycle, "install_dashboard_service", boom)

    req = CreateRequest(
        name="ghost", display_name=None, color=None, provider="anthropic",
        model="m", api_key="k", system_prompt=None, enabled_skills=[],
        api_server_key="s",
    )
    events = [ev async for ev in create_agent(req)]
    assert any(ev.get("event") == "error" for ev in events)
    # profile dir was rolled back
    assert not (fake_env["profiles"] / "ghost").exists()
    # nothing in AGENTS_JSON
    from src.agents_json import read_agents
    entries = read_agents(fake_env["stack"] / ".env")
    assert all(e.id != "ghost" for e in entries)
