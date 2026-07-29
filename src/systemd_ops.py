import os
import shutil
import subprocess
from pathlib import Path

from src.config import Config
from src.profile_ops import _require_single_line_env


_DASHBOARD_UNIT_TEMPLATE = """\
[Unit]
Description=Hermes Agent Dashboard ({name} profile)
After=network.target
# Cap restart attempts so a genuinely broken dashboard doesn't loop forever.
# These are [Unit] keys — in [Service] systemd ignores them with a warning.
StartLimitBurst=10
StartLimitIntervalSec=60

[Service]
Type=simple
Environment=PATH=%h/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
# Loopback only: Hermes refuses a non-loopback bind without an auth provider,
# which turns Restart=always into a crash-loop. The container dashboard reaches
# this via the orchestrator proxy; keep it matching scripts/03-install-profile.sh
# in ollie-hermes-install.
ExecStart=%h/.local/bin/hermes -p {name} dashboard --host 127.0.0.1 --port {port} --insecure --no-open
# Restart=always (not on-failure) so the dashboard comes back even when
# `hermes update` SIGTERMs it (which is a clean exit, status 0).
Restart=always
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


def write_session_token_dropin(unit_name: str) -> bool:
    """Pin this dashboard unit's Hermes session token via a `session-token.conf`
    drop-in. Returns True if written, False when the box has no token.

    Hermes requires its session token on sensitive /api routes whenever the
    dashboard is bound to loopback — `--insecure` waives it only for
    NON-loopback hosts (see sessions.py:_dashboard_headers). Without a pinned
    token the dashboard mints a fresh random one on every start, while the
    orchestrator only ever sends its single HERMES_DASHBOARD_TOKEN. The default
    profile's unit gets a drop-in from scripts/lib/ensure-dashboard-token.sh at
    install time, so `default` worked — but a UI-created agent's unit is written
    AFTER that script ran and never got one, so every management call for it
    (/env, /model/options, config, skills, cron) 401'd. The agent-settings form
    could then neither read nor write a model and silently issued no PATCH.
    Diagnosed live on the Towns box, 2026-07-27.

    The content must stay byte-identical to ensure-dashboard-token.sh — both
    that script and check-box-config.sh's done-done gate compare it exactly.
    """
    token = os.environ.get("HERMES_DASHBOARD_TOKEN", "").strip()
    if not token:
        # Leave today's behaviour alone rather than pin a blank token.
        return False
    _require_single_line_env("HERMES_DASHBOARD_SESSION_TOKEN", token)
    if any(ch.isspace() for ch in token):
        # systemd splits an Environment= value at whitespace, which would pin a
        # silently truncated token — fail loudly instead of shipping that.
        raise ValueError("HERMES_DASHBOARD_TOKEN must not contain whitespace")
    dropdir = _systemd_dir() / f"{unit_name}.d"
    dropdir.mkdir(parents=True, exist_ok=True)
    conf = dropdir / "session-token.conf"
    # No trailing newline, and LF regardless of host platform — the gate does an
    # exact string comparison against printf '[Service]\nEnvironment=...'.
    conf.write_text(
        f"[Service]\nEnvironment=HERMES_DASHBOARD_SESSION_TOKEN={token}",
        newline="\n",
    )
    if os.name == "posix":
        os.chmod(conf, 0o600)
    return True


def install_dashboard_service(name: str, *, port: int) -> None:
    unit_name = f"hermes-dashboard-{name}.service"
    unit_path = _systemd_dir() / unit_name
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(_DASHBOARD_UNIT_TEMPLATE.format(name=name, port=port))
    # Before daemon-reload, so the unit starts with the token already pinned.
    write_session_token_dropin(unit_name)
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
    # Drop the session-token drop-in with it, so a deleted agent leaves no
    # orphaned secret behind for a later agent of the same name to inherit.
    dropdir = _systemd_dir() / f"{unit_name}.service.d"
    if dropdir.is_dir():
        shutil.rmtree(dropdir, ignore_errors=True)
    try:
        _systemctl("daemon-reload")
    except subprocess.CalledProcessError:
        pass
    # `stop` does not clear a unit that was already in `failed` state, so once
    # its file is gone the entry lingers in systemd's state table as
    # `not-found failed` — noise in exactly the surface we read box state from
    # (three such entries after two UI deletes on GetBilled, 2026-07-29; the
    # agents' gateways were failing because they never got API keys). Only
    # reset-failed clears it, and it must run AFTER daemon-reload has dropped
    # the file.
    try:
        _systemctl("reset-failed", unit_name)
    except subprocess.CalledProcessError:
        pass
