import json
import pytest

from src.market_parse import (
    MAX_UPLOAD_BYTES, prepare_user_content, validate_parse_output,
)


def test_max_upload_is_10mb():
    assert MAX_UPLOAD_BYTES == 10 * 1024 * 1024


def test_prepare_csv_decodes_text():
    out = prepare_user_content("stats.csv", b"region,median\nTeravista,450000\n")
    assert isinstance(out, str)
    assert "Teravista,450000" in out
    assert "stats.csv" in out          # filename included for model context


def test_prepare_image_builds_data_uri_block():
    out = prepare_user_content("shot.png", b"\x89PNG fake")
    assert isinstance(out, list)
    assert out[0]["type"] == "image_url"
    assert out[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_prepare_rejects_unsupported_extension():
    with pytest.raises(ValueError):
        prepare_user_content("report.docx", b"anything")


def test_validate_happy_path_and_fence_stripping():
    payload = {
        "label": "Teravista", "period_label": "June 2026", "period_end": "2026-06-30",
        "source_label": "Unlock MLS — June 2026 Market Report",
        "figures": {"medianSoldPrice": "$450,000", "inventoryMonths": "3.1 months",
                    "daysOnMarket": "42 days", "salesVolume": "87 closed sales"},
        "warnings": [],
    }
    text = "```json\n" + json.dumps(payload) + "\n```"
    draft = validate_parse_output(text)
    assert draft["label"] == "Teravista"
    assert draft["figures"]["medianSoldPrice"] == "$450,000"
    assert draft["warnings"] == []


def test_validate_coerces_missing_figures_with_warning():
    text = json.dumps({"label": "X", "period_label": "May 2026",
                       "figures": {"medianSoldPrice": "$400,000"}})
    draft = validate_parse_output(text)
    assert draft["figures"]["daysOnMarket"] == ""            # coerced, not KeyError
    assert draft["period_end"] is None
    assert any("daysOnMarket" in w for w in draft["warnings"])
    assert draft["source_label"] == ""                       # optional, defaults empty


def test_validate_rejects_bad_period_end():
    text = json.dumps({"label": "X", "period_label": "May 2026",
                       "period_end": "junk", "figures": {}})
    draft = validate_parse_output(text)
    assert draft["period_end"] is None                       # invalid date dropped, not fatal


def test_validate_raises_on_garbage():
    with pytest.raises(ValueError):
        validate_parse_output("I could not find any data, sorry!")


def test_validate_tolerates_null_warnings():
    payload = {
        "label": "TestArea", "period_label": "June 2026",
        "figures": {"medianSoldPrice": "$500,000", "inventoryMonths": "2.5",
                    "daysOnMarket": "30", "salesVolume": "100"},
        "warnings": None,
    }
    text = json.dumps(payload)
    draft = validate_parse_output(text)
    assert isinstance(draft["warnings"], list)
    assert len(draft["warnings"]) == 0


def test_validate_coerces_numeric_figure():
    payload = {
        "label": "TestArea", "period_label": "June 2026",
        "figures": {"medianSoldPrice": 450000, "inventoryMonths": 3.1,
                    "daysOnMarket": "42 days", "salesVolume": "87 closed sales"},
    }
    text = json.dumps(payload)
    draft = validate_parse_output(text)
    assert draft["figures"]["medianSoldPrice"] == "450000"
    assert draft["figures"]["inventoryMonths"] == "3.1"
    assert not any("medianSoldPrice" in w for w in draft["warnings"])
    assert not any("inventoryMonths" in w for w in draft["warnings"])
