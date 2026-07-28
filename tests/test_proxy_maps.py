import json
from pathlib import Path

import pytest

from src.agents_json import AgentEntry
from src import proxy_maps


GW = "HERMES_GATEWAY_URLS"
DASH = "HERMES_DASHBOARD_URLS"


def _agent(agent_id, gw, dash):
    return AgentEntry(id=agent_id, name=agent_id, gateway_port=gw,
                      dashboard_port=dash, color="#888888")


def _read_key(path, key):
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            return json.loads(line[len(key) + 1:])
    return None


@pytest.fixture
def env(tmp_path):
    p = tmp_path / "orch.env"
    p.write_text("ORCHESTRATOR_KEY=x\n", encoding="utf-8")
    return p


def test_adds_missing_agents_to_both_maps(env):
    result = proxy_maps.sync(env, [_agent("default", 8642, 9119),
                                   _agent("mail-agent", 8643, 9121)])
    assert _read_key(env, GW) == {"default": "http://127.0.0.1:8642",
                                  "mail-agent": "http://127.0.0.1:8643"}
    assert _read_key(env, DASH) == {"default": "http://127.0.0.1:9119",
                                    "mail-agent": "http://127.0.0.1:9121"}
    assert result["added"] == ["default", "mail-agent"]


def test_never_overwrites_an_operator_pinned_entry(env):
    env.write_text(
        f'{GW}={{"default": "http://10.0.0.5:8642"}}\n', encoding="utf-8")
    proxy_maps.sync(env, [_agent("default", 8642, 9119)])
    assert _read_key(env, GW) == {"default": "http://10.0.0.5:8642"}


def test_preserves_unrelated_operator_entries_when_adding(env):
    env.write_text(
        f'{GW}={{"legacy": "http://127.0.0.1:8000"}}\n', encoding="utf-8")
    proxy_maps.sync(env, [_agent("default", 8642, 9119)])
    assert _read_key(env, GW) == {"legacy": "http://127.0.0.1:8000",
                                  "default": "http://127.0.0.1:8642"}


def test_drop_ids_removes_the_entry(env):
    proxy_maps.sync(env, [_agent("default", 8642, 9119),
                          _agent("mail-agent", 8643, 9121)])
    result = proxy_maps.sync(env, [_agent("default", 8642, 9119)],
                             drop_ids=("mail-agent",))
    assert _read_key(env, GW) == {"default": "http://127.0.0.1:8642"}
    assert _read_key(env, DASH) == {"default": "http://127.0.0.1:9119"}
    assert result["dropped"] == ["mail-agent"]


def test_regenerates_an_unparseable_value(env):
    env.write_text(f"{GW}=not-json\n", encoding="utf-8")
    proxy_maps.sync(env, [_agent("default", 8642, 9119)])
    assert _read_key(env, GW) == {"default": "http://127.0.0.1:8642"}


def test_regenerates_a_non_object_value(env):
    env.write_text(f"{GW}=[]\n", encoding="utf-8")
    proxy_maps.sync(env, [_agent("default", 8642, 9119)])
    assert _read_key(env, GW) == {"default": "http://127.0.0.1:8642"}


def test_is_idempotent(env):
    agents = [_agent("default", 8642, 9119)]
    proxy_maps.sync(env, agents)
    before = env.read_text(encoding="utf-8")
    result = proxy_maps.sync(env, agents)
    assert env.read_text(encoding="utf-8") == before
    assert result == {"added": [], "dropped": []}


def test_absent_file_is_a_no_op(tmp_path):
    missing = tmp_path / "nope" / "orch.env"
    assert proxy_maps.sync(missing, [_agent("default", 8642, 9119)]) == {
        "added": [], "dropped": []}
    assert not missing.exists()


def test_written_value_is_single_line(env):
    proxy_maps.sync(env, [_agent("default", 8642, 9119)])
    gw_lines = [l for l in env.read_text(encoding="utf-8").splitlines()
                if l.startswith(GW + "=")]
    assert len(gw_lines) == 1
