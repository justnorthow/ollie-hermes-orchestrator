import os
import shutil
import pytest
from pathlib import Path


FIXTURES = Path(__file__).parent / "fixtures"
EXT = ".cmd" if os.name == "nt" else ""


# Monkey-patch Path.read_text and Path.write_text to always default to UTF-8
# This ensures consistent behavior across platforms with different locale settings
_original_read_text = Path.read_text
_original_write_text = Path.write_text

def _patched_read_text(self, encoding=None, errors=None):
    if encoding is None:
        encoding = "utf-8"
    return _original_read_text(self, encoding=encoding, errors=errors)

def _patched_write_text(self, data, encoding=None, errors=None):
    if encoding is None:
        encoding = "utf-8"
    return _original_write_text(self, data, encoding=encoding, errors=errors)

Path.read_text = _patched_read_text
Path.write_text = _patched_write_text


@pytest.fixture
def fake_env(monkeypatch, tmp_path):
    """Tmp HOME with fake hermes/systemctl/docker on PATH."""
    home = tmp_path / "home"
    home.mkdir()
    profiles = home / ".hermes" / "profiles"
    profiles.mkdir(parents=True)
    # Default profile config — what inherit_model_config reads to copy into new profiles
    (home / ".hermes" / "config.yaml").write_text(
        "model:\n"
        "  default: gpt-5.5\n"
        "  provider: openai-codex\n"
        "  base_url: https://chatgpt.com/backend-api/codex\n"
    )
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
    monkeypatch.setenv("HERMES_HOME", str(home / ".hermes"))
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
        "hermes_home": home / ".hermes",
        "profiles": profiles,
        "systemd": systemd,
        "stack": stack,
        "logs": log_dir,
    }
