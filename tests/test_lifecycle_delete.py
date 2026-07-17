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
async def test_delete_unknown_agent_is_idempotent(fake_env):
    """A delete for an agent that doesn't exist is a no-op success, not an
    error. The caller asked us to ensure the agent is gone — if it already
    is, the request is satisfied. Prevents 404s from double-click races."""
    result = await delete_agent("ghost")
    assert result["ok"] is True
    assert result.get("already_gone") is True


@pytest.mark.asyncio
async def test_delete_twice_succeeds_both_times(fake_env):
    """Double-click race: two DELETEs land in succession. Both must succeed."""
    await _create("paige")
    first = await delete_agent("paige")
    second = await delete_agent("paige")
    assert first["ok"] is True
    assert second["ok"] is True
    assert second.get("already_gone") is True


@pytest.mark.asyncio
async def test_delete_refuses_default(fake_env):
    result = await delete_agent("default")
    assert result["ok"] is False
    assert "reserved" in result["error"].lower() or "default" in result["error"].lower()


@pytest.mark.asyncio
async def test_delete_does_not_bounce_dashboard_inline(fake_env, monkeypatch):
    """The dashboard bounce must NOT happen inside delete_agent: the container
    houses the nginx proxying the DELETE itself, so an inline bounce severs the
    in-flight response and the browser sees a 502 for a delete that succeeded
    (sandbox 'pam', 2026-07-17). The API layer defers it via BackgroundTasks —
    delete_agent just reports bounce_needed."""
    import src.lifecycle as lifecycle_mod
    await _create("paige")
    calls: list[str] = []
    monkeypatch.setattr(lifecycle_mod, "bounce_dashboard", lambda: calls.append("bounce"))
    result = await delete_agent("paige")
    assert result["ok"] is True
    assert result.get("bounce_needed") is True
    assert calls == []


@pytest.mark.asyncio
async def test_delete_already_gone_needs_no_bounce(fake_env):
    """Nothing changed on an already-gone delete — no reason to bounce."""
    result = await delete_agent("ghost")
    assert result["ok"] is True
    assert result.get("bounce_needed") is not True
