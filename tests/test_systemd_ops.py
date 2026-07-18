from src.systemd_ops import install_gateway_service, install_dashboard_service, \
    stop_and_remove_service


def test_install_gateway_service_calls_per_profile_install(fake_env):
    # First create the profile so the per-profile shim exists on PATH
    from src.profile_ops import create_profile
    create_profile("paige")
    install_gateway_service("paige")
    log = (fake_env["logs"] / "hermes.log").read_text()
    assert "gateway install" in log


def test_install_dashboard_service_writes_unit_and_enables(fake_env):
    install_dashboard_service("paige", port=9121)
    unit = fake_env["systemd"] / "hermes-dashboard-paige.service"
    assert unit.is_file()
    text = unit.read_text()
    assert "--port 9121" in text
    assert "-p paige" in text
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


def test_stop_and_remove_service_disables_and_unlinks(fake_env):
    install_dashboard_service("paige", port=9121)
    stop_and_remove_service("hermes-dashboard-paige")
    unit = fake_env["systemd"] / "hermes-dashboard-paige.service"
    assert not unit.exists()
    log = (fake_env["logs"] / "systemctl.log").read_text()
    assert "stop hermes-dashboard-paige" in log
    assert "disable hermes-dashboard-paige" in log
