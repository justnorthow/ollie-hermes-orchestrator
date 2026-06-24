from src.api.extractors import parse_compliance, EXTRACTORS

PASS_OUT = """Field Notes — May 2026.

Buyers have more choice.

```compliance
STATUS: PASS
```"""

FLAGGED_OUT = """Body text here.

```compliance
STATUS: FLAGGED
- text: "perfect for a young family"
  rule: Fair Housing — familial status
  citation: TREC §531.x
  rewrite: "spacious layout near parks and schools"
```"""


def test_registry_has_compliance_screen():
    assert EXTRACTORS["compliance_screen"] is parse_compliance


def test_parse_pass_strips_block_and_keeps_body():
    r = parse_compliance(PASS_OUT)
    assert r["status"] == "pass"
    assert r["findings"] == []
    assert "Buyers have more choice." in r["content"]
    assert "```compliance" not in r["content"]
    assert "STATUS:" not in r["content"]


def test_parse_flagged_extracts_items():
    r = parse_compliance(FLAGGED_OUT)
    assert r["status"] == "flagged"
    assert len(r["findings"]) == 1
    f = r["findings"][0]
    assert f["text"] == "perfect for a young family"
    assert f["rule"].startswith("Fair Housing")
    assert f["citation"] == "TREC §531.x"
    assert f["rewrite"].startswith("spacious layout")
    assert "Body text here." in r["content"]


def test_status_variants_and_unknown():
    assert parse_compliance("x\n```compliance\nSTATUS: NEEDS HUMAN REVIEW\n```")["status"] == "needs_review"
    assert parse_compliance("no fenced block here")["status"] == "unknown"
    assert parse_compliance("no fenced block here")["content"] == "no fenced block here"
