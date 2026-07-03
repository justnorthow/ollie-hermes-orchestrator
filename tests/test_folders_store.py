from pathlib import Path

from src.config import Config
from src.folders_store import read_folders, write_folders


def _cfg(tmp_path: Path) -> Config:
    return Config(
        orchestrator_key="k",
        hermes_stack_dir=tmp_path / "stack",
        hermes_home=tmp_path / "hermes",
        hermes_profiles_dir=tmp_path / "hermes" / "profiles",
        systemd_user_dir=tmp_path / "systemd",
        audit_log_path=tmp_path / "audit.log",
        instance_id="default",
    )


def test_read_empty_when_missing(tmp_path):
    assert read_folders(_cfg(tmp_path)) == []


def test_write_then_read_roundtrip(tmp_path):
    cfg = _cfg(tmp_path)
    folders = [{"id": "a", "name": "Listings", "order": 0, "appIds": ["home-inspection-advisor"]}]
    write_folders(cfg, folders)
    assert read_folders(cfg) == folders
