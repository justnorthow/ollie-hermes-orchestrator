from src.docker_ops import bounce_dashboard


def test_bounce_dashboard_invokes_compose(fake_env):
    bounce_dashboard()
    log = (fake_env["logs"] / "docker.log").read_text()
    assert "compose" in log
    assert "up -d dashboard" in log
