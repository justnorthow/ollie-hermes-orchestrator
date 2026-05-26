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


def test_stop_and_remove_service_disables_and_unlinks(fake_env):
    install_dashboard_service("paige", port=9121)
    stop_and_remove_service("hermes-dashboard-paige")
    unit = fake_env["systemd"] / "hermes-dashboard-paige.service"
    assert not unit.exists()
    log = (fake_env["logs"] / "systemctl.log").read_text()
    assert "stop hermes-dashboard-paige" in log
    assert "disable hermes-dashboard-paige" in log
