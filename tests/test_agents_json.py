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


def test_write_agent_handles_non_ascii_name(tmp_path):
    """json.dumps emits \\uXXXX escapes for non-ASCII names; feeding that to
    re.sub as a raw replacement raised 'bad escape \\u'. Must round-trip."""
    env = tmp_path / ".env"
    _write_env(env, 'HERMES_GATEWAY_KEY=k\nAGENTS_JSON=[]\n')
    write_agent(env, AgentEntry(
        id="cafe", name="Café", gateway_port=8643, dashboard_port=9121, color="#abc",
    ))
    assert "HERMES_GATEWAY_KEY=k" in env.read_text()
    entries = read_agents(env)
    assert entries[0].name == "Café"


def test_write_agent_roundtrips_backslash_in_name(tmp_path):
    """A literal backslash in a name must survive; re.sub replacement-escape
    processing previously mangled it."""
    env = tmp_path / ".env"
    _write_env(env, 'AGENTS_JSON=[]\n')
    write_agent(env, AgentEntry(
        id="x", name=r"a\b", gateway_port=8643, dashboard_port=9121, color="#abc",
    ))
    entries = read_agents(env)
    assert entries[0].name == r"a\b"


def test_remove_agent_drops_entry(tmp_path):
    env = tmp_path / ".env"
    _write_env(env, 'AGENTS_JSON=[{"id":"a","name":"A","gatewayUrl":"http://host.docker.internal:8643","dashboardUrl":"http://host.docker.internal:9121"},{"id":"b","name":"B","gatewayUrl":"http://host.docker.internal:8644","dashboardUrl":"http://host.docker.internal:9122"}]\n')
    remove_agent(env, "a")
    entries = read_agents(env)
    assert [e.id for e in entries] == ["b"]


def test_entry_parses_scope_and_manager_visible():
    from src.agents_json import _json_to_entry
    e = _json_to_entry({"id": "default", "name": "Ollie",
                        "gatewayUrl": "http://h:8642", "dashboardUrl": "http://h:9119",
                        "scope": "user", "manager_visible": True})
    assert e.scope == "user"
    assert e.manager_visible is True


def test_entry_defaults_scope_company():
    from src.agents_json import _json_to_entry
    e = _json_to_entry({"id": "pam", "name": "Pam",
                        "gatewayUrl": "http://h:8643", "dashboardUrl": "http://h:9121"})
    assert e.scope == "company"
    assert e.manager_visible is False


def test_entry_roundtrips_scope():
    from src.agents_json import AgentEntry, _entry_to_json, _json_to_entry
    e = AgentEntry(id="pam", name="Pam", gateway_port=8643, dashboard_port=9121,
                   color="#888888", model=None, scope="company", manager_visible=True)
    out = _entry_to_json(e)
    assert out["scope"] == "company"
    assert out["manager_visible"] is True
    assert _json_to_entry(out).manager_visible is True


def test_set_env_key_appends_when_absent(tmp_path):
    from src.agents_json import set_env_key
    env = tmp_path / ".env"
    env.write_text("HERMES_GATEWAY_KEY=k\n")
    set_env_key(env, "INSTANCE_TITLE", "JNOW Prod")
    text = env.read_text()
    assert "INSTANCE_TITLE=JNOW Prod\n" in text
    assert text.startswith("HERMES_GATEWAY_KEY=k\n")


def test_set_env_key_replaces_in_place_and_collapses_duplicates(tmp_path):
    from src.agents_json import set_env_key
    env = tmp_path / ".env"
    env.write_text("A=1\nINSTANCE_TITLE=Old\nB=2\nINSTANCE_TITLE=Older\n")
    set_env_key(env, "INSTANCE_TITLE", "New")
    lines = env.read_text().splitlines()
    assert lines.count("INSTANCE_TITLE=New") == 1
    assert "INSTANCE_TITLE=Old" not in lines and "INSTANCE_TITLE=Older" not in lines
    assert "A=1" in lines and "B=2" in lines


def test_set_env_key_empty_value_keeps_key(tmp_path):
    from src.agents_json import set_env_key
    env = tmp_path / ".env"
    env.write_text("INSTANCE_TITLE=Old\n")
    set_env_key(env, "INSTANCE_TITLE", "")
    assert "INSTANCE_TITLE=\n" in env.read_text()


def test_set_env_key_value_with_backslashes_survives(tmp_path):
    # Same re.sub backslash-escape trap _replace_agents_line guards against.
    from src.agents_json import set_env_key
    env = tmp_path / ".env"
    env.write_text("INSTANCE_TITLE=Old\n")
    set_env_key(env, "INSTANCE_TITLE", r"C:\Users\weird ሴ")
    assert r"INSTANCE_TITLE=C:\Users\weird ሴ" in env.read_text(encoding="utf-8")


def test_subtitle_round_trips():
    from src.agents_json import AgentEntry, _entry_to_json, _json_to_entry
    e = AgentEntry(id="olivia", name="Olivia", gateway_port=9200, dashboard_port=9201,
                   color="#e879f9", subtitle="AI Head of Marketing")
    d = _entry_to_json(e)
    assert d["subtitle"] == "AI Head of Marketing"
    assert _json_to_entry(d).subtitle == "AI Head of Marketing"


def test_subtitle_absent_stays_absent():
    from src.agents_json import AgentEntry, _entry_to_json, _json_to_entry
    e = AgentEntry(id="ollie", name="Ollie", gateway_port=9100, dashboard_port=9101,
                   color="#a78bfa")
    d = _entry_to_json(e)
    assert "subtitle" not in d          # back-compat: no key emitted when unset
    assert _json_to_entry(d).subtitle is None


def test_legacy_json_without_subtitle_parses():
    from src.agents_json import _json_to_entry
    d = {"id": "x", "name": "X", "gatewayUrl": "http://host.docker.internal:9100",
         "dashboardUrl": "http://host.docker.internal:9101", "color": "#888888"}
    assert _json_to_entry(d).subtitle is None


def test_avatar_url_round_trips():
    from src.agents_json import AgentEntry, _entry_to_json, _json_to_entry
    e = AgentEntry(id="ollie", name="Ollie", gateway_port=9100, dashboard_port=9101,
                   color="#a78bfa", avatar_url="https://x/shared/ollie.jpg?t=1")
    d = _entry_to_json(e)
    assert d["avatar_url"] == "https://x/shared/ollie.jpg?t=1"
    assert _json_to_entry(d).avatar_url == "https://x/shared/ollie.jpg?t=1"


def test_avatar_url_absent_stays_absent():
    from src.agents_json import AgentEntry, _entry_to_json, _json_to_entry
    e = AgentEntry(id="ollie", name="Ollie", gateway_port=9100, dashboard_port=9101,
                   color="#a78bfa")
    d = _entry_to_json(e)
    assert "avatar_url" not in d
    assert _json_to_entry(d).avatar_url is None


def test_legacy_json_without_avatar_url_parses():
    from src.agents_json import _json_to_entry
    d = {"id": "x", "name": "X", "gatewayUrl": "http://host.docker.internal:9100",
         "dashboardUrl": "http://host.docker.internal:9101", "color": "#888888"}
    assert _json_to_entry(d).avatar_url is None


def test_voice_round_trips(tmp_path):
    env = tmp_path / ".env"
    env.write_text("AGENTS_JSON=[]\n")
    write_agent(env, AgentEntry(
        id="default", name="Ollie", gateway_port=8642, dashboard_port=9119,
        color="#888888", voice="en-GB-RyanNeural",
    ))
    entries = read_agents(env)
    assert entries[0].voice == "en-GB-RyanNeural"
    # serialized compactly under the "voice" key
    assert '"voice":"en-GB-RyanNeural"' in env.read_text()


def test_voice_absent_is_none_and_not_serialized(tmp_path):
    env = tmp_path / ".env"
    env.write_text("AGENTS_JSON=[]\n")
    write_agent(env, AgentEntry(
        id="default", name="Ollie", gateway_port=8642, dashboard_port=9119,
        color="#888888",
    ))
    assert read_agents(env)[0].voice is None
    assert '"voice"' not in env.read_text()
