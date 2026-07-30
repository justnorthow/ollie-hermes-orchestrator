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
        # "default" when INSTANCE_ID is unset, which DIVERGES from
        # src/api/sessions.py::_instance_filter(), where the same unset var
        # yields a PostgREST `instance_id IS NULL` filter. The divergence is
        # safe because the two address different tables and each is internally
        # consistent:
        #   * user_roles / role_labels are BOTH written and read through this
        #     value (roles.set_tier / resolve_tier, always via cfg.instance_id),
        #     so on a box with no INSTANCE_ID the rows say "default" and the
        #     lookups ask for "default".
        #   * agent_sessions rows are written WITHOUT an instance_id when the
        #     var is unset (sessions.record_session) and read back with
        #     `is.null`, which is likewise self-consistent.
        # This matters now that dispatch decides access on the resolved tier
        # (src/dispatch/roster.py::visible_to): a mismatch here would silently
        # resolve every admin to `member` and deny them. It does not, and the
        # same resolve_tier(cfg.instance_id, ...) call already gates the
        # dashboard via src/api/authz.py — an admin silently downgraded here
        # would already be losing agents from their dashboard, visibly.
        # Real boxes set INSTANCE_ID explicitly (docs/runbooks/rbac-phase2a-rollout.md
        # step 2); "default" is the dev/unset fallback. Do NOT "fix" this by
        # making one side match the other without migrating the existing rows
        # in whichever table you changed.
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
