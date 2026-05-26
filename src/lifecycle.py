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
    api_key: str
    system_prompt: Optional[str]
    enabled_skills: list[str]
    api_server_key: str


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

    try:
        with file_lock(cfg.hermes_stack_dir / ".agents.lock"):
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
            provider_var = _PROVIDER_ENV_VAR.get(req.provider, f"{req.provider.upper()}_API_KEY")
            write_profile_env(
                req.name,
                provider_creds={provider_var: req.api_key},
                api_server_port=ports.gateway,
                api_server_key=req.api_server_key,
            )
            yield _ev("write_profile_env")
            completed_steps.append("write_profile_env")

            # 5. apply per-profile config
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
