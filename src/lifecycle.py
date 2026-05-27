import logging
import time
from dataclasses import dataclass
from typing import AsyncIterator, Optional

from src.agents_json import AgentEntry, read_agents, write_agent, remove_agent
from src.config import Config
from src.docker_ops import bounce_dashboard
from src.lock import file_lock
from src.models import Agent
from src.ports import allocate_ports
from src.profile_ops import create_profile, delete_profile, write_profile_env, set_config
from src.systemd_ops import install_gateway_service, install_dashboard_service, \
    stop_and_remove_service

_logger = logging.getLogger(__name__)

_PROVIDER_ENV_VAR = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
}

_DEFAULT_PALETTE = ["#6366f1", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#14b8a6"]


@dataclass
class CreateRequest:
    name: str
    display_name: Optional[str]
    color: Optional[str]
    provider: str
    model: str
    api_key: Optional[str]
    system_prompt: Optional[str]
    enabled_skills: list[str]
    api_server_key: str
    # "api_key" writes provider creds into the profile .env (default).
    # "inherit" skips them so Hermes uses whatever auth the host has already
    # configured (Codex OAuth, Claude Code CLI, ambient env vars).
    auth_method: str = "api_key"


def _pick_color(existing_colors: list[str]) -> str:
    for c in _DEFAULT_PALETTE:
        if c not in existing_colors:
            return c
    return _DEFAULT_PALETTE[0]


async def create_agent(req: CreateRequest) -> AsyncIterator[dict]:
    started = time.monotonic()
    cfg = Config.load()
    env_path = cfg.hermes_stack_dir / ".env"
    completed_steps: list[str] = []

    def _ev(step: str, **extras) -> dict:
        return {"step": step, "ok": True, **extras}

    with file_lock(cfg.hermes_stack_dir / ".agents.lock"):
        try:
            # 1. validate
            existing = read_agents(env_path)
            if any(e.id == req.name for e in existing):
                yield {"event": "error", "step": "validate",
                       "error": f"agent '{req.name}' already exists"}
                return
            yield _ev("validate")
            completed_steps.append("validate")

            # 2. allocate ports
            ports = allocate_ports(
                existing_gateways=[e.gateway_port for e in existing],
                existing_dashboards=[e.dashboard_port for e in existing],
            )
            yield _ev("allocate_ports", gateway=ports.gateway, dashboard=ports.dashboard)
            completed_steps.append("allocate_ports")

            # 3. hermes profile create
            create_profile(req.name)
            yield _ev("create_profile")
            completed_steps.append("create_profile")

            # 4. write profile .env
            # Only write provider creds when auth_method is "api_key". For
            # "inherit", leave provider env unset so Hermes picks up OAuth
            # tokens or ambient credentials from the host environment.
            if req.auth_method == "api_key" and req.api_key:
                provider_var = _PROVIDER_ENV_VAR.get(req.provider, f"{req.provider.upper()}_API_KEY")
                provider_creds = {provider_var: req.api_key}
            else:
                provider_creds = {}
            write_profile_env(
                req.name,
                provider_creds=provider_creds,
                api_server_port=ports.gateway,
                api_server_key=req.api_server_key,
            )
            yield _ev("write_profile_env", auth_method=req.auth_method)
            completed_steps.append("write_profile_env")

            # 5. apply per-profile config
            # Model is optional when inheriting host credentials — Hermes uses
            # its own default in that case. Only call set_config if user picked one.
            if req.model:
                set_config(req.name, "model", req.model)
            set_config(req.name, "gateway.port", str(ports.gateway))
            if req.system_prompt:
                set_config(req.name, "system_prompt", req.system_prompt)
            yield _ev("apply_config")
            completed_steps.append("apply_config")

            # 6. install gateway service
            install_gateway_service(req.name)
            yield _ev("install_gateway")
            completed_steps.append("install_gateway")

            # 7. install dashboard service
            install_dashboard_service(req.name, port=ports.dashboard)
            yield _ev("install_dashboard")
            completed_steps.append("install_dashboard")

            # 8. update AGENTS_JSON
            color = req.color or _pick_color([e.color for e in existing])
            entry = AgentEntry(
                id=req.name,
                name=req.display_name or req.name.capitalize(),
                gateway_port=ports.gateway,
                dashboard_port=ports.dashboard,
                color=color,
                model=req.model,
            )
            write_agent(env_path, entry)
            yield _ev("update_agents_json")
            completed_steps.append("update_agents_json")

            # 9. bounce dashboard container
            bounce_dashboard()
            yield _ev("bounce_dashboard")
            completed_steps.append("bounce_dashboard")

            # 10. done
            agent = Agent(
                id=req.name,
                displayName=entry.name,
                color=color,
                provider=req.provider,
                model=req.model,
                gatewayPort=ports.gateway,
                dashboardPort=ports.dashboard,
                systemPrompt=req.system_prompt,
                enabledSkills=req.enabled_skills,
            )
            yield {"event": "done", "agent": agent.model_dump(),
                   "duration_ms": int((time.monotonic() - started) * 1000)}

        except Exception as exc:
            _logger.exception("create_agent failed at step=%s",
                              completed_steps[-1] if completed_steps else "?")
            yield {"event": "error",
                   "step": completed_steps[-1] if completed_steps else "init",
                   "error": str(exc)}
            await _rollback_create(req.name, completed_steps, env_path)


async def _rollback_create(name: str, completed_steps: list[str], env_path) -> None:
    if "update_agents_json" in completed_steps:
        try:
            remove_agent(env_path, name)
        except Exception:
            _logger.warning("rollback: remove_agent failed", exc_info=True)
    if "install_dashboard" in completed_steps:
        try:
            stop_and_remove_service(f"hermes-dashboard-{name}")
        except Exception:
            _logger.warning("rollback: dashboard svc failed", exc_info=True)
    if "install_gateway" in completed_steps:
        try:
            stop_and_remove_service(f"hermes-gateway-{name}")
        except Exception:
            _logger.warning("rollback: gateway svc failed", exc_info=True)
    if "create_profile" in completed_steps:
        try:
            delete_profile(name)
        except Exception:
            _logger.warning("rollback: delete_profile failed", exc_info=True)


@dataclass
class UpdateRequest:
    displayName: Optional[str] = None
    color: Optional[str] = None
    model: Optional[str] = None
    systemPrompt: Optional[str] = None
    enabledSkills: Optional[list[str]] = None
    apiKey: Optional[str] = None
    restart: bool = True


_RESTART_REQUIRED = {"model", "systemPrompt", "enabledSkills", "apiKey"}
_RESERVED_AGENT_IDS = {"default"}


async def delete_agent(agent_id: str) -> dict:
    if agent_id in _RESERVED_AGENT_IDS:
        return {"ok": False, "error": "agent_id is reserved (default profile)"}
    cfg = Config.load()
    env_path = cfg.hermes_stack_dir / ".env"
    with file_lock(cfg.hermes_stack_dir / ".agents.lock"):
        existing = read_agents(env_path)
        if not any(e.id == agent_id for e in existing):
            return {"ok": False, "error": "not_found"}
        # tear down in reverse-create order
        try:
            stop_and_remove_service(f"hermes-dashboard-{agent_id}")
        except Exception:
            _logger.warning("delete: dashboard svc failed", exc_info=True)
        try:
            stop_and_remove_service(f"hermes-gateway-{agent_id}")
        except Exception:
            _logger.warning("delete: gateway svc failed", exc_info=True)
        try:
            delete_profile(agent_id)
        except Exception:
            _logger.warning("delete: profile dir failed", exc_info=True)
        try:
            remove_agent(env_path, agent_id)
        except Exception:
            _logger.warning("delete: AGENTS_JSON failed", exc_info=True)
        try:
            bounce_dashboard()
        except Exception:
            _logger.warning("delete: dashboard bounce failed", exc_info=True)
        return {"ok": True}


async def update_agent(agent_id: str, req: "UpdateRequest") -> dict:
    cfg = Config.load()
    env_path = cfg.hermes_stack_dir / ".env"
    with file_lock(cfg.hermes_stack_dir / ".agents.lock"):
        existing = read_agents(env_path)
        entry = next((e for e in existing if e.id == agent_id), None)
        if entry is None:
            return {"ok": False, "error": "not_found"}

        # which fields actually changed (non-None means caller set them)
        changed = {k: v for k, v in vars(req).items() if v is not None and k != "restart"}
        needs_restart = bool(set(changed) & _RESTART_REQUIRED)

        # Apply profile-config changes
        if req.model is not None:
            set_config(agent_id, "model", req.model)
        if req.systemPrompt is not None:
            set_config(agent_id, "system_prompt", req.systemPrompt)
        if req.apiKey is not None:
            provider_var = "ANTHROPIC_API_KEY"  # TODO: read existing provider
            write_profile_env(
                agent_id,
                provider_creds={provider_var: req.apiKey},
                api_server_port=entry.gateway_port,
                api_server_key="shared",
            )

        # Apply AGENTS_JSON changes
        new_entry = AgentEntry(
            id=entry.id,
            name=req.displayName if req.displayName is not None else entry.name,
            gateway_port=entry.gateway_port,
            dashboard_port=entry.dashboard_port,
            color=req.color if req.color is not None else entry.color,
            model=req.model if req.model is not None else entry.model,
        )
        write_agent(env_path, new_entry)

        restarted = False
        if needs_restart and req.restart:
            try:
                _systemctl_restart(f"hermes-gateway-{agent_id}")
                restarted = True
            except Exception:
                _logger.warning("update: gateway restart failed", exc_info=True)

        return {"ok": True, "restarted": restarted}


def _systemctl_restart(unit: str) -> None:
    import subprocess
    import shutil
    bin_ = shutil.which("systemctl") or "systemctl"
    subprocess.run([bin_, "--user", "restart", unit], check=True)
