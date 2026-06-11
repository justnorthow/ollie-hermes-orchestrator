import os
import shutil
import subprocess
from pathlib import Path

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


def read_profile_env(name: str) -> dict[str, str]:
    """Parse a profile's .env into a dict. Returns {} if the file is missing.

    Used on update to preserve fields write_profile_env would otherwise
    overwrite (API_SERVER_KEY, host, CORS) and to detect which provider env
    var the profile already uses."""
    path = _profiles_dir() / name / ".env"
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        result[k.strip()] = v.strip()
    return result


def set_config(name: str, key: str, value: str) -> None:
    """Run `<name> config set <key> <value>` (Hermes' per-profile CLI shim)."""
    subprocess.run([_resolve(name), "config", "set", key, value], check=True)


def get_default_config(key: str) -> str | None:
    """Read a dotted-path value from the DEFAULT Hermes profile's config.yaml.
    Hermes itself has no `config get` subcommand (only show/set/path), so we
    parse the YAML directly. Returns None if the key is unset or the file is
    missing/unreadable."""
    config_path = Path(os.environ.get("HOME", os.path.expanduser("~"))) / ".hermes" / "config.yaml"
    if not config_path.is_file():
        return None
    try:
        import yaml
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return None
    # Walk the dotted path
    node = data
    for part in key.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
        if node is None:
            return None
    return str(node) if node != "" else None


# Model-level config keys that define which LLM the agent calls. Copied wholesale
# from the default profile into new profiles when authMethod=inherit.
_INHERITED_MODEL_KEYS = ("model.default", "model.provider", "model.base_url")


def inherit_model_config(name: str) -> dict[str, str]:
    """Copy the default profile's model.* config keys into the named profile.
    Used when the new agent should "use existing credentials" — Hermes treats
    each profile's model config independently, so without this the new profile
    has no provider and errors with 'No inference provider configured' on the
    first chat. Returns the dict of keys that were applied for logging."""
    applied: dict[str, str] = {}
    for key in _INHERITED_MODEL_KEYS:
        value = get_default_config(key)
        if value:
            set_config(name, key, value)
            applied[key] = value
    return applied
