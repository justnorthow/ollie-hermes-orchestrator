"""The Hermes UI proxy listener must follow agent create/delete.

Agents created through this API (which is what BOTH the dashboard UI and
`ollie-fleetctl agents create` use) got no hermes-ui-proxy listener at all: the
render lived only in fleetctl's _create_dashboard_unit, a path the API never
reaches. Measured on the sandbox 2026-07-30 — the new agent's unit existed, the
conf did not, and the operator had no browser access to that agent's dashboard
until someone ran the install script by hand.

These tests drive a REAL bash subprocess against a stub script rather than
asserting on a mock, so they prove render() actually executes the script with
the environment the script reads.
"""
import os
import subprocess
from pathlib import Path

import pytest

from src.lifecycle import CreateRequest, create_agent, delete_agent


@pytest.fixture
def posix_bash(tmp_path):
    """Skip unless `bash` can actually execute a script under tmp_path.

    render() shells out to `bash <script>`, which is correct on the Linux boxes
    this ships to. On a Windows dev machine `bash` resolves to WSL's, which
    cannot see C:/ paths, so these two tests would fail for an environment
    reason rather than a code one. Probe rather than guess — the probe is the
    same call render() makes.
    """
    probe = tmp_path / "probe.sh"
    probe.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    probe.chmod(0o755)
    try:
        rc = subprocess.run(["bash", probe.as_posix()],
                            capture_output=True, timeout=30).returncode
    except Exception:
        rc = 1
    if rc != 0:
        pytest.skip("no bash on PATH that can execute a script under tmp_path "
                    "(WSL bash cannot see Windows paths) — this test is "
                    "meaningful on Linux/CI")


def _req(name="mail-agent"):
    return CreateRequest(
        name=name, display_name="Karl M", color=None, provider="openai",
        model="gpt-5.6-sol", api_key="k", system_prompt=None,
        enabled_skills=[], api_server_key="gk", auth_method="inherit",
    )


@pytest.fixture
def orch_env(monkeypatch, tmp_path):
    p = tmp_path / "orch.env"
    p.write_text("ORCHESTRATOR_KEY=x\nHERMES_DASHBOARD_TOKEN=tok-123\n",
                 encoding="utf-8")
    monkeypatch.setenv("ORCH_ENV", str(p))
    return p


@pytest.fixture
def stub_install_dir(monkeypatch, tmp_path):
    """An install tree whose ensure-hermes-ui-proxy.sh records each invocation."""
    install = tmp_path / "install"
    lib = install / "scripts" / "lib"
    lib.mkdir(parents=True)
    script = lib / "ensure-hermes-ui-proxy.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$ORCH_ENV" >> "$RENDER_LOG"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("INSTALL_DIR", str(install))
    monkeypatch.setenv("RENDER_LOG", str(install / "renders.log"))
    return install


def _render_count(install):
    log = install / "renders.log"
    if not log.exists():
        return 0
    return len([ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()])


async def test_create_renders_the_ui_proxy(fake_env, orch_env, stub_install_dir, posix_bash):
    events = [ev async for ev in create_agent(_req())]
    assert events[-1]["event"] == "done", events[-1]
    assert _render_count(stub_install_dir) >= 1, (
        "create never rendered the hermes-ui proxy, so the new agent has no "
        "browser-reachable dashboard"
    )


async def test_delete_rerenders_so_the_listener_is_pruned(
        fake_env, orch_env, stub_install_dir, posix_bash):
    events = [ev async for ev in create_agent(_req())]
    assert events[-1]["event"] == "done", events[-1]
    after_create = _render_count(stub_install_dir)
    assert (await delete_agent("mail-agent"))["ok"] is True
    assert _render_count(stub_install_dir) > after_create, (
        "delete never re-rendered, so the deleted agent's listener stays bound "
        "to a dead upstream"
    )


async def test_create_still_succeeds_when_the_render_fails(
        fake_env, orch_env, stub_install_dir, posix_bash):
    """Browser access is a convenience; it must never break agent creation.
    The done-done gate catches a missing conf on its next run and fails closed."""
    script = stub_install_dir / "scripts" / "lib" / "ensure-hermes-ui-proxy.sh"
    script.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    script.chmod(0o755)
    events = [ev async for ev in create_agent(_req())]
    assert events[-1]["event"] == "done", events[-1]


async def test_create_still_succeeds_when_the_script_is_absent(
        fake_env, orch_env, monkeypatch, tmp_path):
    """Older boxes have no ensure-hermes-ui-proxy.sh at all."""
    monkeypatch.setenv("INSTALL_DIR", str(tmp_path / "nonexistent"))
    events = [ev async for ev in create_agent(_req())]
    assert events[-1]["event"] == "done", events[-1]
