import json
from pathlib import Path
from src.agents_json import read_agents, write_agent, remove_agent, AgentEntry


def _write_env(path: Path, content: str) -> None:
    path.write_text(content)


def test_read_agents_parses_existing_entries(tmp_path):
    env = tmp_path / ".env"
    _write_env(env, 'HERMES_GATEWAY_KEY=k\n'
                    'AGENTS_JSON=[{"id":"default","name":"Ollie",'
                    '"gatewayUrl":"http://host.docker.internal:8642","dashboardUrl":"http://host.docker.internal:9119"}]\n')
    entries = read_agents(env)
    assert len(entries) == 1
    assert entries[0].id == "default"
    assert entries[0].gateway_port == 8642
    assert entries[0].dashboard_port == 9119


def test_write_agent_appends_atomically(tmp_path):
    env = tmp_path / ".env"
    _write_env(env, 'HERMES_GATEWAY_KEY=k\nAGENTS_JSON=[]\n')
    write_agent(env, AgentEntry(
        id="paige", name="Paige", gateway_port=8643, dashboard_port=9121, color="#abc",
    ))
    text = env.read_text()
    assert "HERMES_GATEWAY_KEY=k" in text  # other vars untouched
    parsed = json.loads(text.split("AGENTS_JSON=", 1)[1].strip())
    assert len(parsed) == 1
    assert parsed[0]["id"] == "paige"
    assert parsed[0]["gatewayUrl"] == "http://host.docker.internal:8643"


def test_write_agent_is_atomic_under_failure(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    _write_env(env, 'AGENTS_JSON=[]\n')
    orig = env.read_text()

    def boom(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr("os.replace", boom)
    try:
        write_agent(env, AgentEntry(id="x", name="X", gateway_port=8643, dashboard_port=9121, color="#abc"))
    except OSError:
        pass
    # original content preserved
    assert env.read_text() == orig


def test_remove_agent_drops_entry(tmp_path):
    env = tmp_path / ".env"
    _write_env(env, 'AGENTS_JSON=[{"id":"a","name":"A","gatewayUrl":"http://host.docker.internal:8643","dashboardUrl":"http://host.docker.internal:9121"},{"id":"b","name":"B","gatewayUrl":"http://host.docker.internal:8644","dashboardUrl":"http://host.docker.internal:9122"}]\n')
    remove_agent(env, "a")
    entries = read_agents(env)
    assert [e.id for e in entries] == ["b"]
