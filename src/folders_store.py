import json
import logging
from pathlib import Path

from src.config import Config

_logger = logging.getLogger(__name__)


def _folders_path(cfg: Config) -> Path:
    return cfg.hermes_home / "folders.json"


def read_folders(cfg: Config) -> list[dict]:
    path = _folders_path(cfg)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return data.get("folders", []) if isinstance(data, dict) else []
    except (json.JSONDecodeError, OSError):
        _logger.warning("folders.json unreadable, returning empty list")
        return []


def write_folders(cfg: Config, folders: list[dict]) -> None:
    path = _folders_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"folders": folders}, indent=2))
