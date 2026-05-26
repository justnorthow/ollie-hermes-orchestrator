import pytest
from src.models import CreateAgent, Agent, validate_agent_name


def test_validate_agent_name_accepts_simple():
    validate_agent_name("paige")
    validate_agent_name("agent-2")


@pytest.mark.parametrize("bad", ["", "A", "1agent", "agent_x", "ag", "a" * 32, "../etc", "default", "system", "admin"])
def test_validate_agent_name_rejects(bad):
    with pytest.raises(ValueError):
        validate_agent_name(bad)


def test_create_agent_minimal_valid():
    a = CreateAgent(name="carla", provider="anthropic", model="claude-sonnet-4.6", apiKey="sk-x")
    assert a.name == "carla"
    assert a.displayName is None


def test_create_agent_rejects_bad_name():
    with pytest.raises(ValueError):
        CreateAgent(name="bad name", provider="anthropic", model="x", apiKey="sk-x")


def test_agent_redacts_api_key_on_dump():
    a = Agent(
        id="carla", displayName="Carla", color="#00aabb",
        provider="anthropic", model="claude-sonnet-4.6",
        gatewayPort=8644, dashboardPort=9122,
        enabledSkills=["github-repo-management"], systemPrompt="be helpful",
    )
    dumped = a.model_dump()
    assert "apiKey" not in dumped
