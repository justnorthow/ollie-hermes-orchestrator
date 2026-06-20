import json
import logging
import os
import tempfile
from pathlib import Path

from src.config import Config
from src.lock import file_lock

_logger = logging.getLogger(__name__)

_FOLDERS_LOCK = "folders.lock"


def _folders_path(cfg: Config) -> Path:
    return cfg.hermes_home / "folders.json"


def read_folders(cfg: Config) -> list[dict]:
    path = _folders_path(cfg)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("folders", []) if isinstance(data, dict) else []
    except (json.JSONDecodeError, OSError):
        _logger.warning("folders.json unreadable, returning empty list")
        return []


def write_folders(cfg: Config, folders: list[dict]) -> None:
    path = _folders_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / _FOLDERS_LOCK
    text = json.dumps({"folders": folders}, indent=2)
    with file_lock(lock_path):
        fd, tmp = tempfile.mkstemp(prefix=".folders.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
