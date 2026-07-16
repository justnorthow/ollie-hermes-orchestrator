import os
import shutil
import subprocess

from src.config import Config

# Docker compose gives PROCESS-ENV values precedence over the project .env
# file when interpolating ${VARS} in docker-compose.yml. The orchestrator's
# own environment legitimately carries SUPABASE_URL=http://127.0.0.1:8000
# (loopback Kong — correct for the orchestrator's JWKS fetches) plus other
# stack keys; letting them leak into a compose invocation recreates the
# dashboard with the wrong values baked into config.js and breaks browser
# login (live incident 2026-07-16, all three boxes). Compose must therefore
# run with only this allowlist — everything else interpolates from the
# stack .env, the single source of truth.
_ENV_ALLOWLIST = (
    "HOME", "PATH", "USER", "LOGNAME",
    # docker client plumbing
    "DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY",
    # Windows dev-box essentials so the CLI can spawn at all under tests
    "SYSTEMROOT", "COMSPEC", "USERPROFILE", "TEMP", "TMP", "PATHEXT",
    "FAKE_DOCKER_LOG",  # test harness: fake-docker writes its call log here
)


def _sanitized_env() -> dict[str, str]:
    return {k: os.environ[k] for k in _ENV_ALLOWLIST if os.environ.get(k)}


def _resolve(bin_name: str) -> str:
    return shutil.which(bin_name) or bin_name


def bounce_dashboard() -> None:
    """Restart the dashboard container so nginx regenerates its agent proxies."""
    cfg = Config.load()
    compose_file = cfg.hermes_stack_dir / "docker-compose.yml"
    subprocess.run(
        [_resolve("docker"), "compose", "-f", str(compose_file), "up", "-d", "dashboard"],
        check=True,
        env=_sanitized_env(),
    )
