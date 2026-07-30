"""Rendering of the Hermes UI proxy listeners (ollie-hermes-install's
scripts/lib/ensure-hermes-ui-proxy.sh).

That script publishes each agent's loopback Hermes dashboard on `upstream + 100`
behind an nginx listener that injects the `Authorization: Bearer <session token>`
header a browser cannot send on navigation. It discovers agents from the
hermes-dashboard*.service units and converges the rendered confs to that set —
adding a listener for a new unit and pruning one whose unit is gone.

Historically it was called only by the install scripts and by ollie-fleetctl's
_create_dashboard_unit. An agent created through THIS API — the path taken by
both the dashboard UI and `ollie-fleetctl agents create`, which proxies to it —
got no listener at all, so the operator had no browser-reachable dashboard for
that agent until someone ran the script by hand (sandbox acceptance run,
2026-07-30). Delete had the mirror problem: the listener stayed bound to an
upstream that no longer existed.

Best-effort, deliberately, exactly like proxy_maps: the listener is an operator
convenience and the agent is fully functional (chat, API, gateway) without it, so
a render failure must never fail a create or leave a delete half-done.
check-box-config.sh section 3b fails closed on a missing or stale conf, which is
what actually guarantees the box converges.
"""
import logging
import os
import subprocess
from pathlib import Path

_logger = logging.getLogger(__name__)

SCRIPT_REL = Path("scripts") / "lib" / "ensure-hermes-ui-proxy.sh"
DEFAULT_TIMEOUT = 60


def install_dir() -> Path:
    """Where ollie-hermes-install is checked out on this box."""
    home = Path(os.environ.get("HOME", os.path.expanduser("~")))
    return Path(os.environ.get("INSTALL_DIR", home / "ollie-hermes-install"))


def render(orch_env: Path, systemd_user_dir: Path, *,
           timeout: int = DEFAULT_TIMEOUT) -> bool:
    """Re-render every hermes-ui-proxy listener. True when the script ran clean.

    Returns False (never raises) when the script is absent — older boxes predate
    it — or when it fails, so callers can log and carry on.
    """
    script = install_dir() / SCRIPT_REL
    if not script.is_file():
        _logger.info("hermes-ui proxy render skipped: %s is not present", script)
        return False

    env = dict(os.environ)
    env["ORCH_ENV"] = str(orch_env)
    env["SYSTEMD_USER_DIR"] = str(systemd_user_dir)
    try:
        proc = subprocess.run(
            # as_posix(), not str(): identical on the Linux boxes this runs on,
            # but it keeps the path usable by git-bash on a Windows dev machine,
            # which otherwise eats the backslashes and reports "No such file".
            ["bash", script.as_posix()],
            env=env, capture_output=True, text=True, timeout=timeout,
        )
    except Exception:
        _logger.warning("hermes-ui proxy render could not be run", exc_info=True)
        return False

    if proc.returncode != 0:
        _logger.warning(
            "hermes-ui proxy render exited %s — the box will report a missing or "
            "stale hermes-ui-proxy conf until this is resolved: %s",
            proc.returncode, (proc.stderr or "").strip()[:500],
        )
        return False
    return True
