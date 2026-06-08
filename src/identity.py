"""Identity persistence: write an agent's SOUL.md and detect the first-run marker."""
from __future__ import annotations
import os
import tempfile
from pathlib import Path

DEFAULT_MARKER = "OLLIE-SOUL-DEFAULT"


def write_soul(soul_path: Path, content: str) -> None:
    """Atomically write SOUL.md (temp file + os.replace), mode 0644."""
    soul_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(soul_path.parent), prefix=".soul_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(tmp, 0o644)
        os.replace(tmp, soul_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def soul_needs_identity(soul_path: Path) -> bool:
    """True if the agent still needs first-run identity setup: SOUL is missing or still
    carries the OLLIE-SOUL-DEFAULT marker. Best-effort: unreadable -> False (don't nag)."""
    try:
        if not soul_path.exists():
            return True
        return DEFAULT_MARKER in soul_path.read_text(encoding="utf-8")
    except OSError:
        return False


def resolve_soul_path(agent_id: str, hermes_home: Path, profiles_dir: Path) -> Path:
    """default -> {hermes_home}/SOUL.md ; others -> {profiles_dir}/{id}/SOUL.md."""
    if agent_id == "default":
        return Path(hermes_home) / "SOUL.md"
    return Path(profiles_dir) / agent_id / "SOUL.md"
