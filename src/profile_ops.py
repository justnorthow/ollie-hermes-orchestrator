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
    """Run `hermes profile delete <name>` and best-effort clean the dir.

    Hermes' delete is interactive — it lists what will be deleted and asks
    the user to type the profile name to confirm. We pipe the name into
    stdin to satisfy that prompt. If Hermes fails for any reason (e.g.
    binary missing during rollback), we fall back to removing the profile
    directory directly."""
    subprocess.run(
        [_resolve(_HERMES_BIN), "profile", "delete", name],
        check=False,
        input=f"{name}\n",
        text=True,
    )
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
