import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    orchestrator_key: str
    hermes_stack_dir: Path
    hermes_home: Path
    hermes_profiles_dir: Path
    systemd_user_dir: Path
    audit_log_path: Path
    instance_id: str
    orch_env_path: Path

    @classmethod
    def load(cls) -> "Config":
        key = os.environ.get("ORCHESTRATOR_KEY", "").strip()
        if not key:
            raise ConfigError("ORCHESTRATOR_KEY is not set")
        home = Path(os.environ.get("HOME", os.path.expanduser("~")))
        stack = Path(os.environ.get("HERMES_STACK_DIR", home / "hermes-stack"))
        hermes_home = Path(os.environ.get("HERMES_HOME", home / ".hermes"))
        profiles = Path(os.environ.get("HERMES_PROFILES_DIR", hermes_home / "profiles"))
        systemd_dir = Path(os.environ.get("SYSTEMD_USER_DIR", home / ".config" / "systemd" / "user"))
        audit = Path(os.environ.get("AUDIT_LOG_PATH",
                                    home / ".local" / "state" / "ollie-orchestrator" / "audit.log"))
        instance_id = os.environ.get("INSTANCE_ID", "").strip() or "default"
        orch_env = Path(os.environ.get(
            "ORCH_ENV", home / ".config" / "ollie-orchestrator" / ".env"))
        return cls(
            orchestrator_key=key,
            hermes_stack_dir=stack,
            hermes_home=hermes_home,
            hermes_profiles_dir=profiles,
            systemd_user_dir=systemd_dir,
            audit_log_path=audit,
            instance_id=instance_id,
            orch_env_path=orch_env,
        )
