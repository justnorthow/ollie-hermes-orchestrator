import os

import pytest

from src.systemd_ops import install_gateway_service, install_dashboard_service, \
    stop_and_remove_service, write_runtime_sandbox_dropin


def test_install_gateway_service_calls_per_profile_install(fake_env):
    # First create the profile so the per-profile shim exists on PATH
    from src.profile_ops import create_profile
    create_profile("paige")
    install_gateway_service("paige")
    log = (fake_env["logs"] / "hermes.log").read_text()
    assert "gateway install" in log
    conf = (fake_env["systemd"] / "hermes-gateway-paige.service.d"
            / "20-ollie-runtime-sandbox.conf")
    assert conf.is_file()
    text = conf.read_text()
    assert "NoNewPrivileges=yes" in text
    assert "ProtectHome=tmpfs" in text
    assert "InaccessiblePaths=-/var/run/docker.sock" in text
    systemctl = (fake_env["logs"] / "systemctl.log").read_text()
    assert "daemon-reload" in systemctl
    assert "try-restart hermes-gateway-paige.service" in systemctl


def test_runtime_sandbox_rejects_path_traversal_unit(fake_env):
    with pytest.raises(ValueError):
        write_runtime_sandbox_dropin("hermes-gateway-x/../../escape.service")


def test_install_dashboard_service_writes_unit_and_enables(fake_env):
    install_dashboard_service("paige", port=9121)
    unit = fake_env["systemd"] / "hermes-dashboard-paige.service"
    assert unit.is_file()
    text = unit.read_text()
    assert "--port 9121" in text
    assert "-p paige" in text
    sandbox = (fake_env["systemd"] / "hermes-dashboard-paige.service.d"
               / "20-ollie-runtime-sandbox.conf")
    assert sandbox.is_file()
    assert "NoNewPrivileges=yes" in sandbox.read_text()
    log = (fake_env["logs"] / "systemctl.log").read_text()
    assert "daemon-reload" in log
    assert "enable --now hermes-dashboard-paige" in log


def test_install_dashboard_service_binds_loopback(fake_env):
    # Hermes refuses a non-loopback bind with no auth provider configured, so a
    # 0.0.0.0 unit crash-loops under Restart=always (the 2026-07-18 pam outage:
    # 7,086 restarts + npm-ci OOM sawtooth took down the jnow prod tunnel).
    install_dashboard_service("paige", port=9121)
    unit = fake_env["systemd"] / "hermes-dashboard-paige.service"
    exec_line = next(
        line for line in unit.read_text().splitlines()
        if line.startswith("ExecStart=")
    )
    assert "--host 127.0.0.1" in exec_line
    assert "0.0.0.0" not in exec_line


def test_install_dashboard_service_start_limit_in_unit_section(fake_env):
    # StartLimitBurst/StartLimitIntervalSec are [Unit] keys; in [Service] systemd
    # ignores them with a warning, leaving the restart loop uncapped.
    install_dashboard_service("paige", port=9121)
    unit = fake_env["systemd"] / "hermes-dashboard-paige.service"
    lines = unit.read_text().splitlines()
    unit_lines = lines[:lines.index("[Service]")]
    service_lines = lines[lines.index("[Service]"):]
    assert any(l.startswith("StartLimitBurst=") for l in unit_lines)
    assert any(l.startswith("StartLimitIntervalSec=") for l in unit_lines)
    assert not any(l.startswith("StartLimit") for l in service_lines)


def test_install_dashboard_service_writes_session_token_dropin(fake_env, monkeypatch):
    # Without this drop-in the per-profile dashboard mints a RANDOM session token
    # at every start, while the orchestrator only ever sends its single
    # HERMES_DASHBOARD_TOKEN. The DEFAULT unit gets a drop-in from
    # scripts/lib/ensure-dashboard-token.sh at install time, but a UI-created
    # agent's unit is written afterwards and never got one — so every management
    # call for it (/env, /model/options, config, skills, cron) 401'd and the
    # agent-settings form silently issued no PATCH. NOTE --insecure does NOT
    # waive the token on a loopback bind. Live-hit on the Towns box 2026-07-27.
    monkeypatch.setenv("HERMES_DASHBOARD_TOKEN", "s3cr3t-token")
    install_dashboard_service("paige", port=9121)
    conf = (fake_env["systemd"] / "hermes-dashboard-paige.service.d"
            / "session-token.conf")
    assert conf.is_file()
    # Byte-exact against ensure-dashboard-token.sh and the check-box-config gate,
    # which both compare with printf '[Service]\nEnvironment=...' — no trailing
    # newline, LF only.
    assert conf.read_bytes() == (
        b"[Service]\nEnvironment=HERMES_DASHBOARD_SESSION_TOKEN=s3cr3t-token"
    )


def test_install_dashboard_service_omits_dropin_when_token_unset(fake_env, monkeypatch):
    # Writing an empty value would pin the dashboard to a blank token, which is
    # worse than the randomized one: skip the drop-in and leave today's
    # behaviour untouched on boxes that never provisioned a token.
    monkeypatch.delenv("HERMES_DASHBOARD_TOKEN", raising=False)
    install_dashboard_service("paige", port=9121)
    conf = (fake_env["systemd"] / "hermes-dashboard-paige.service.d"
            / "session-token.conf")
    assert not conf.exists()


def test_install_dashboard_service_rejects_whitespace_token(fake_env, monkeypatch):
    # systemd splits Environment= at whitespace, which would pin a silently
    # truncated token — reproducing the same 401 the drop-in exists to prevent.
    monkeypatch.setenv("HERMES_DASHBOARD_TOKEN", "tok with space")
    with pytest.raises(ValueError):
        install_dashboard_service("paige", port=9121)


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes only")
def test_session_token_dropin_not_world_readable(fake_env, monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_TOKEN", "s3cr3t-token")
    install_dashboard_service("paige", port=9121)
    conf = (fake_env["systemd"] / "hermes-dashboard-paige.service.d"
            / "session-token.conf")
    assert conf.stat().st_mode & 0o077 == 0


def test_stop_and_remove_service_removes_token_dropin(fake_env, monkeypatch):
    # A deleted agent must not leave its secret behind for a later agent of the
    # same name to inherit.
    monkeypatch.setenv("HERMES_DASHBOARD_TOKEN", "s3cr3t-token")
    install_dashboard_service("paige", port=9121)
    stop_and_remove_service("hermes-dashboard-paige")
    assert not (fake_env["systemd"] / "hermes-dashboard-paige.service.d").exists()


def test_stop_and_remove_service_disables_and_unlinks(fake_env):
    install_dashboard_service("paige", port=9121)
    stop_and_remove_service("hermes-dashboard-paige")
    unit = fake_env["systemd"] / "hermes-dashboard-paige.service"
    assert not unit.exists()
    log = (fake_env["logs"] / "systemctl.log").read_text()
    assert "stop hermes-dashboard-paige" in log
    assert "disable hermes-dashboard-paige" in log


def test_stop_and_remove_service_clears_failed_state(fake_env):
    """`stop` does NOT clear a unit that was already in `failed` state, so the
    entry survives in systemd's state table as `not-found failed` long after its
    file is gone. Deleting two throwaway agents through the UI on the GetBilled
    box (2026-07-29) left three such entries behind — noise in exactly the
    surface we read box state from. Only `reset-failed` clears them, and it must
    run AFTER daemon-reload has dropped the unit file."""
    install_dashboard_service("paige", port=9121)
    stop_and_remove_service("hermes-dashboard-paige")
    log = (fake_env["logs"] / "systemctl.log").read_text()
    assert "reset-failed hermes-dashboard-paige" in log
    assert log.index("daemon-reload") < log.index("reset-failed hermes-dashboard-paige")
