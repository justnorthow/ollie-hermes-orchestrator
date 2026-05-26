import os
import shutil
import pytest
from pathlib import Path


FIXTURES = Path(__file__).parent / "fixtures"
EXT = ".cmd" if os.name == "nt" else ""


@pytest.fixture
def fake_env(monkeypatch, tmp_path):
    """Tmp HOME with fake hermes/systemctl/docker on PATH."""
    home = tmp_path / "home"
    home.mkdir()
    profiles = home / ".hermes" / "profiles"
    profiles.mkdir(parents=True)
    systemd = home / ".config" / "systemd" / "user"
    systemd.mkdir(parents=True)
    stack = home / "hermes-stack"
    stack.mkdir()
    (stack / ".env").write_text("HERMES_GATEWAY_KEY=k\nAGENTS_JSON=[]\n")
    (stack / "docker-compose.yml").write_text("services:\n  dashboard: {}\n")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("fake-hermes", "fake-systemctl", "fake-docker"):
        src = FIXTURES / f"{name}{EXT}"
        dest = bindir / (name.replace("fake-", "") + EXT)
        shutil.copy(src, dest)
        dest.chmod(0o755)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILES_DIR", str(profiles))
    monkeypatch.setenv("SYSTEMD_USER_DIR", str(systemd))
    monkeypatch.setenv("HERMES_STACK_DIR", str(stack))
    monkeypatch.setenv("ORCHESTRATOR_KEY", "topsecret")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_TEST_BINDIR", str(bindir))

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("FAKE_HERMES_LOG", str(log_dir / "hermes.log"))
    monkeypatch.setenv("FAKE_SYSTEMCTL_LOG", str(log_dir / "systemctl.log"))
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log_dir / "docker.log"))

    return {
        "home": home,
        "profiles": profiles,
        "systemd": systemd,
        "stack": stack,
        "logs": log_dir,
    }
