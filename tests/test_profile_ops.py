from pathlib import Path
import pytest

from src.profile_ops import (
    create_profile, delete_profile, inherit_default_auth, write_profile_env,
)


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


def test_write_profile_env_rejects_multiline_values(fake_env):
    create_profile("paige")
    with pytest.raises(ValueError, match="single-line"):
        write_profile_env(
            "paige",
            provider_creds={"ANTHROPIC_API_KEY": "sk-ok\nMALICIOUS=true"},
            api_server_port=9110,
            api_server_key="shared",
        )


def test_delete_profile_removes_dir(fake_env):
    create_profile("paige")
    assert (fake_env["profiles"] / "paige").is_dir()
    delete_profile("paige")
    assert not (fake_env["profiles"] / "paige").exists()


def test_set_config_invokes_per_profile_shim(fake_env):
    from src.profile_ops import set_config
    create_profile("paige")
    set_config("paige", "model", "claude-sonnet-4.6")
    log = (fake_env["logs"] / "hermes.log").read_text()
    # The per-profile shim is created during `profile create paige` and writes
    # to the same log file. We expect the config set call to be recorded.
    assert "config set model claude-sonnet-4.6" in log


def test_inherit_default_auth_links_shared_credentials(fake_env, monkeypatch):
    create_profile("paige")
    (fake_env["hermes_home"] / "auth.json").write_text('{"access_token":"x"}')
    calls = []

    def fake_symlink(source, target):
        calls.append((source, Path(target)))

    monkeypatch.setattr("src.profile_ops.os.symlink", fake_symlink)
    assert inherit_default_auth("paige") is True
    assert Path(calls[0][0]) == Path("..") / ".." / "auth.json"
    assert calls[0][1] == fake_env["profiles"] / "paige" / "auth.json"


def test_inherit_default_auth_is_optional(fake_env, monkeypatch):
    create_profile("paige")
    monkeypatch.setattr(
        "src.profile_ops.os.symlink",
        lambda *_: pytest.fail("must not link a missing auth file"),
    )
    assert inherit_default_auth("paige") is False
