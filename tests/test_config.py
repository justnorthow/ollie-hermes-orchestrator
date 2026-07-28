import os
import pytest
from src.config import Config, ConfigError


def test_load_requires_orchestrator_key(monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_KEY", raising=False)
    with pytest.raises(ConfigError):
        Config.load()


def test_load_uses_defaults_when_paths_unset(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCHESTRATOR_KEY", "secret")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_STACK_DIR", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.delenv("HERMES_PROFILES_DIR", raising=False)
    monkeypatch.delenv("SYSTEMD_USER_DIR", raising=False)

    cfg = Config.load()
    assert cfg.orchestrator_key == "secret"
    assert cfg.hermes_stack_dir == tmp_path / "hermes-stack"
    assert cfg.hermes_profiles_dir == tmp_path / ".hermes" / "profiles"
    assert cfg.systemd_user_dir == tmp_path / ".config" / "systemd" / "user"


def test_load_honors_path_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCHESTRATOR_KEY", "secret")
    monkeypatch.setenv("HERMES_STACK_DIR", str(tmp_path / "stack"))
    cfg = Config.load()
    assert cfg.hermes_stack_dir == tmp_path / "stack"


def test_instance_id_defaults_and_reads_env(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_KEY", "k")
    monkeypatch.delenv("INSTANCE_ID", raising=False)
    from src.config import Config
    assert Config.load().instance_id == "default"
    monkeypatch.setenv("INSTANCE_ID", "sandbox")
    assert Config.load().instance_id == "sandbox"


def test_orch_env_path_defaults_under_home(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCHESTRATOR_KEY", "topsecret")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ORCH_ENV", raising=False)
    cfg = Config.load()
    assert cfg.orch_env_path == tmp_path / ".config" / "ollie-orchestrator" / ".env"


def test_orch_env_path_honours_orch_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCHESTRATOR_KEY", "topsecret")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ORCH_ENV", str(tmp_path / "custom.env"))
    cfg = Config.load()
    assert cfg.orch_env_path == tmp_path / "custom.env"
