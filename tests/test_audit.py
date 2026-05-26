import json

from src.audit import audit


def test_audit_appends_jsonline(tmp_path):
    log = tmp_path / "audit.log"
    audit(log, op="create", agent_id="paige", actor_ip="127.0.0.1",
          result="ok", duration_ms=1234)
    lines = log.read_text().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["op"] == "create"
    assert parsed["agent_id"] == "paige"
    assert "ts" in parsed


def test_audit_redacts_unknown_extras(tmp_path):
    log = tmp_path / "audit.log"
    audit(log, op="create", agent_id="x", actor_ip="1.2.3.4",
          result="error", duration_ms=0, error="boom",
          api_key="should-not-appear")
    text = log.read_text()
    assert "should-not-appear" not in text
