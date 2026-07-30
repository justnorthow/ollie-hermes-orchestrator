from src.dispatch.types import (
    MODE_DIRECT,
    MODE_OFF,
    REASON_FORBIDDEN,
    REASON_TIMEOUT,
    VALID_MODES,
    ConsultRequest,
    ConsultResult,
    Teammate,
)


def test_modes_are_the_four_the_spec_names():
    assert VALID_MODES == {"off", "direct", "local", "linear"}
    assert MODE_OFF == "off" and MODE_DIRECT == "direct"


def test_refusal_reasons_are_distinct_strings():
    from src.dispatch import types

    reasons = [v for k, v in vars(types).items() if k.startswith("REASON_")]
    assert len(reasons) == len(set(reasons)), "reason constants must be unique"
    assert all(isinstance(r, str) and r for r in reasons)


def test_granted_result_carries_the_answer():
    r = ConsultResult.granted("72F and sunny", peer="karl-m")

    assert r.ok is True
    assert r.answer == "72F and sunny"
    assert r.reason is None
    assert r.peer == "karl-m"


def test_refused_result_has_no_answer_and_names_a_reason():
    r = ConsultResult.refused(REASON_TIMEOUT, detail="peer took >30s", peer="karl-m")

    assert r.ok is False
    assert r.answer is None
    assert r.reason == REASON_TIMEOUT
    assert "30s" in r.detail


def test_refused_never_fabricates_an_answer_even_when_detail_is_empty():
    r = ConsultResult.refused(REASON_FORBIDDEN)

    assert r.ok is False
    assert r.answer is None
    assert r.detail == ""


def test_consult_request_chain_defaults_empty_and_is_hashable():
    req = ConsultRequest(
        from_agent="billie", session_id="sess-1", to_agent="karl-m", question="hi"
    )

    assert req.chain == ()
    hash(req)  # frozen dataclasses must be hashable — cycle detection uses sets


def test_teammate_records_consult_eligibility():
    t = Teammate("karl-m", "Karl M", "Email", "gpt-5.6-terra", "fast", True)

    assert t.consult_eligible is True
    assert t.speed_class == "fast"
