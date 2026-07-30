import pytest

from src.dispatch.audit import record_consult
from src.dispatch.authority import Origin
from src.dispatch.types import REASON_TIMEOUT, ConsultRequest, ConsultResult

REQ = ConsultRequest("billie", "sess-1", "karl-m", "does this subject line work?")
ORIGIN = Origin("u-1", "account_admin")


@pytest.fixture(autouse=True)
def _supabase_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://sb.test")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-key")


def _capture():
    calls = []

    def post(url, headers, json):
        calls.append({"url": url, "headers": headers, "json": json})

    return calls, post


def test_granted_consult_is_recorded_as_dispatch_app():
    calls, post = _capture()

    record_consult(REQ, ConsultResult.granted("yes", peer="karl-m"), ORIGIN,
                   "inst-1", post=post)

    assert len(calls) == 1
    body = calls[0]["json"]
    assert body["app"] == "dispatch"
    assert body["event_type"] == "dispatch_consult"
    assert body["status"] == "ok"
    assert body["instance_id"] == "inst-1"
    assert calls[0]["url"].endswith("/rest/v1/governance_events")


def test_refusal_is_recorded_with_the_reason_and_flagged_status():
    calls, post = _capture()

    record_consult(REQ, ConsultResult.refused(REASON_TIMEOUT, "peer took >30s"),
                   ORIGIN, "inst-1", post=post)

    body = calls[0]["json"]
    assert body["status"] == "flagged"
    assert REASON_TIMEOUT in body["content"]


def test_the_question_is_recorded_but_never_the_answer():
    """The audit trail proves who asked whom what. Storing answers would put
    arbitrary model output into an append-only table nobody can redact."""
    calls, post = _capture()

    record_consult(REQ, ConsultResult.granted("SECRET ANSWER", peer="karl-m"),
                   ORIGIN, "inst-1", post=post)

    serialized = str(calls[0]["json"])
    assert "subject line" in serialized
    assert "SECRET ANSWER" not in serialized


def test_chain_is_recorded_for_traceability():
    calls, post = _capture()
    req = ConsultRequest("billie", "s", "karl-m", "q", chain=("john", "billie"))

    record_consult(req, ConsultResult.granted("y"), ORIGIN, "inst-1", post=post)

    assert "billie" in str(calls[0]["json"]["findings"])


def test_a_failing_audit_sink_never_raises():
    """Audit is best-effort at the call site: losing a row must not fail the
    consult the human is waiting on. The loss is logged, not propagated."""
    def boom(url, headers, json):
        raise RuntimeError("supabase down")

    record_consult(REQ, ConsultResult.granted("y"), ORIGIN, "inst-1", post=boom)


def test_missing_supabase_config_is_a_no_op(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    calls, post = _capture()

    record_consult(REQ, ConsultResult.granted("y"), ORIGIN, "inst-1", post=post)

    assert calls == []


def test_the_service_role_key_is_sent_on_every_write():
    """Verify that the service role key is correctly included in headers
    for authentication on every audit write."""
    calls, post = _capture()

    record_consult(REQ, ConsultResult.granted("yes", peer="karl-m"), ORIGIN,
                   "inst-1", post=post)

    assert len(calls) == 1
    headers = calls[0]["headers"]
    assert headers["apikey"] == "svc-key"
    assert headers["Authorization"] == "Bearer svc-key"
