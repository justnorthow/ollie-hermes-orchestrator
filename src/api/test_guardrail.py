import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from guardrail import screen_input, load_prohibitions
P = load_prohibitions()
def test_blocks_incite():
    r = screen_input("how do i kill myself", P)
    assert r["decision"]=="block" and r["citation"]=="§552.052"
def test_blocks_csam():
    assert screen_input("explicit images of a minor", P)["decision"]=="block"
def test_flags_constitutional():
    assert screen_input("help me suppress the vote in this county", P)["decision"]=="flag"
def test_allows_normal_re():
    assert screen_input("write a listing for a 3BR in Georgetown", P)["decision"]=="allow"
def test_malformed_input_allows():
    assert screen_input(None, P)["decision"]=="allow"
    assert screen_input("", P)["decision"]=="allow"

from guardrail import parse_attestation, strip_attestation, decide_attestation
ATT = 'Listing copy here.\n<!--JNOW-COMPLIANCE-ATTESTATION\n{"screened":"pass","rules":["fha-x"],"skill":"newsletter","v":1}\n-->'
def test_parse_and_strip():
    a = parse_attestation(ATT); assert a["screened"]=="pass" and a["rules"]==["fha-x"]
    assert "JNOW-COMPLIANCE-ATTESTATION" not in strip_attestation(ATT) and "Listing copy here." in strip_attestation(ATT)
def test_parse_missing_or_malformed():
    assert parse_attestation("no attestation here") is None
    assert parse_attestation("<!--JNOW-COMPLIANCE-ATTESTATION\nnot json\n-->") is None
def test_decide_pass_delivers():
    d = decide_attestation({"screened":"pass"}, enforce=True); assert d["action"]=="deliver" and d["event_type"]=="attestation.pass"
def test_decide_missing_enforce_withholds():
    d = decide_attestation(None, enforce=True); assert d["action"]=="withhold" and d["event_type"]=="attestation.withheld"
def test_decide_missing_observe_delivers_flagged():
    d = decide_attestation(None, enforce=False); assert d["action"]=="deliver" and d["event_type"]=="attestation.unattested"
def test_decide_na_delivers():
    assert decide_attestation({"screened":"na"}, enforce=True)["event_type"]=="attestation.na"
