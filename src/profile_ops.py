import os
import shutil
import subprocess

from src.config import Config


_HERMES_BIN = "hermes"


def _resolve(bin_name: str) -> str:
    """Resolve a binary on PATH. On Windows this picks up PATHEXT (.cmd/.bat/.exe)."""
    resolved = shutil.which(bin_name)
    return resolved if resolved else bin_name


def _profiles_dir():
    return Config.load().hermes_profiles_dir


def create_profile(name: str) -> None:
    """Run `hermes profile create <name>`."""
    subprocess.run([_resolve(_HERMES_BIN), "profile", "create", name], check=True)


def delete_profile(name: str) -> None:
    """Remove the profile directory."""
    subprocess.run([_resolve(_HERMES_BIN), "profile", "delete", name], check=False)
    target = _profiles_dir() / name
    if target.exists():
        shutil.rmtree(target)


def write_profile_env(
    name: str,
    *,
    provider_creds: dict[str, str],
    api_server_port: int,
    api_server_key: str,
    api_server_host: str = "0.0.0.0",
    api_server_cors: str = "*",
) -> None:
    path = _profiles_dir() / name / ".env"
    lines = [f"{k}={v}" for k, v in provider_creds.items()]
    lines += [
        "API_SERVER_ENABLED=true",
        f"API_SERVER_HOST={api_server_host}",
        f"API_SERVER_PORT={api_server_port}",
        f"API_SERVER_KEY={api_server_key}",
        f"API_SERVER_CORS_ORIGINS={api_server_cors}",
    ]
    path.write_text("\n".join(lines) + "\n")
    if os.name == "posix":
        os.chmod(path, 0o600)


def set_config(name: str, key: str, value: str) -> None:
    """Run `<name> config set <key> <value>` (Hermes' per-profile CLI shim)."""
    subprocess.run([_resolve(name), "config", "set", key, value], check=True)
