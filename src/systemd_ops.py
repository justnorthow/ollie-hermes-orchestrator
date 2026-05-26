import os
import shutil
import subprocess
from pathlib import Path

from src.config import Config


_DASHBOARD_UNIT_TEMPLATE = """\
[Unit]
Description=Hermes Agent Dashboard ({name} profile)
After=network.target

[Service]
Type=simple
Environment=PATH=%h/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=%h/.local/bin/hermes -p {name} dashboard --host 0.0.0.0 --port {port} --insecure --no-open
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def _systemd_dir() -> Path:
    return Config.load().systemd_user_dir


def _resolve(bin_name: str) -> str:
    return shutil.which(bin_name) or bin_name


def _systemctl(*args: str) -> None:
    subprocess.run([_resolve("systemctl"), "--user", *args], check=True)


def install_gateway_service(name: str) -> None:
    """Run `<name> gateway install`. Hermes asks two interactive prompts:
      1. "Start the gateway now after installing the service? [Y/n]"
      2. "Start the gateway automatically on login/boot with systemd? [Y/n]"
    Feed a generous supply of 'y' answers so we cover both prompts (and any
    future ones Hermes adds) without blocking on stdin."""
    subprocess.run(
        [_resolve(name), "gateway", "install"],
        check=True,
        input="y\n" * 10,
        text=True,
    )


def install_dashboard_service(name: str, *, port: int) -> None:
    unit_path = _systemd_dir() / f"hermes-dashboard-{name}.service"
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(_DASHBOARD_UNIT_TEMPLATE.format(name=name, port=port))
    _systemctl("daemon-reload")
    _systemctl("enable", "--now", f"hermes-dashboard-{name}")


def stop_and_remove_service(unit_name: str) -> None:
    """Disable + stop + unlink. Idempotent."""
    try:
        _systemctl("stop", unit_name)
    except subprocess.CalledProcessError:
        pass
    try:
        _systemctl("disable", unit_name)
    except subprocess.CalledProcessError:
        pass
    unit_path = _systemd_dir() / f"{unit_name}.service"
    if unit_path.exists():
        unit_path.unlink()
    try:
        _systemctl("daemon-reload")
    except subprocess.CalledProcessError:
        pass
