from pathlib import Path
from src.profile_ops import create_profile, delete_profile, write_profile_env


def test_create_profile_invokes_hermes_and_creates_dir(fake_env):
    create_profile("paige")
    assert (fake_env["profiles"] / "paige").is_dir()
    log = (fake_env["logs"] / "hermes.log").read_text()
    assert "profile create paige" in log


def test_write_profile_env_writes_mode_0600(fake_env):
    create_profile("paige")
    write_profile_env(
        "paige",
        provider_creds={"ANTHROPIC_API_KEY": "sk-x"},
        api_server_port=8643,
        api_server_key="shared",
    )
    p = fake_env["profiles"] / "paige" / ".env"
    text = p.read_text()
    assert "ANTHROPIC_API_KEY=sk-x" in text
    assert "API_SERVER_ENABLED=true" in text
    assert "API_SERVER_PORT=8643" in text
    assert "API_SERVER_KEY=shared" in text
    import os
    if os.name == "posix":
        assert oct(p.stat().st_mode)[-3:] == "600"


def test_delete_profile_removes_dir(fake_env):
    create_profile("paige")
    assert (fake_env["profiles"] / "paige").is_dir()
    delete_profile("paige")
    assert not (fake_env["profiles"] / "paige").exists()
