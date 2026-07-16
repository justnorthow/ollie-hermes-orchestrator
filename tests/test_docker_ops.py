import subprocess

from src.docker_ops import bounce_dashboard


def test_bounce_dashboard_invokes_compose(fake_env):
    bounce_dashboard()
    log = (fake_env["logs"] / "docker.log").read_text()
    assert "compose" in log
    assert "up -d dashboard" in log


def test_bounce_dashboard_sanitizes_environment(fake_env, monkeypatch):
    # Compose gives process-env values precedence over the project .env file.
    # The orchestrator's own env carries SUPABASE_URL=http://127.0.0.1:8000
    # (loopback, correct for the orchestrator) — if it leaks into the compose
    # invocation it poisons the recreated dashboard's config.js and breaks
    # browser login (live incident 2026-07-16 on all three boxes).
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("HERMES_GATEWAY_KEY", "orch-process-value")
    captured: dict = {}
    real_run = subprocess.run

    def spy_run(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy_run)
    bounce_dashboard()
    env = captured["env"]
    assert env is not None, "bounce must pass an explicit sanitized env"
    assert "SUPABASE_URL" not in env
    assert "HERMES_GATEWAY_KEY" not in env
    assert "PATH" in env and "HOME" in env
