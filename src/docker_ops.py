import shutil
import subprocess

from src.config import Config


def _resolve(bin_name: str) -> str:
    return shutil.which(bin_name) or bin_name


def bounce_dashboard() -> None:
    """Restart the dashboard container so nginx regenerates its agent proxies."""
    cfg = Config.load()
    compose_file = cfg.hermes_stack_dir / "docker-compose.yml"
    subprocess.run(
        [_resolve("docker"), "compose", "-f", str(compose_file), "up", "-d", "dashboard"],
        check=True,
    )
