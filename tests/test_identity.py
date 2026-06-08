from pathlib import Path
from src.identity import write_soul, soul_needs_identity, resolve_soul_path


def test_write_soul_atomic(tmp_path):
    p = tmp_path / "SOUL.md"
    write_soul(p, "You are Billie.")
    assert p.read_text() == "You are Billie."


def test_needs_identity_marker(tmp_path):
    p = tmp_path / "SOUL.md"
    assert soul_needs_identity(p) is True          # missing
    p.write_text("<!-- OLLIE-SOUL-DEFAULT -->\n# stub")
    assert soul_needs_identity(p) is True           # marker present
    p.write_text("You are Billie.")
    assert soul_needs_identity(p) is False           # real persona


def test_resolve_soul_path(tmp_path):
    home = tmp_path / "hermes"
    profiles = home / "profiles"
    assert resolve_soul_path("default", home, profiles) == home / "SOUL.md"
    assert resolve_soul_path("paige", home, profiles) == profiles / "paige" / "SOUL.md"
