from datetime import date

import pytest

from src.catalog_check.providers import ScrapeConfig
from src.catalog_check.sinks import render_report, write_file_sink
from src.catalog_check.types import Diff, ProviderResult

TODAY = date(2026, 7, 29)


@pytest.fixture(autouse=True)
def _no_linear_credentials(monkeypatch):
    """Every test in this module must run offline. Clear Linear credentials so
    run() cannot reach the network even on a machine that has them set."""
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("LINEAR_TEAM_ID", raising=False)


def test_report_names_unknown_ids_first():
    diff = Diff(unknown=[("anthropic", "claude-bogus")], new=[("openai", "gpt-5.6-luna")])

    report = render_report(diff, TODAY, {"anthropic": "scrape"})

    assert "claude-bogus" in report
    assert report.index("claude-bogus") < report.index("gpt-5.6-luna")


def test_report_includes_adoption_checklist_for_each_new_model():
    diff = Diff(new=[("openai", "gpt-5.6-luna")])

    report = render_report(diff, TODAY, {})

    assert "gpt-5.6-luna" in report
    assert "speed" in report.lower()
    assert "price" in report.lower()


def test_report_states_mechanism_per_provider():
    report = render_report(Diff(), TODAY, {"anthropic": "scrape", "openai": "none"})

    assert "anthropic" in report and "scrape" in report
    assert "openai" in report and "none" in report


def test_report_names_unverifiable_providers():
    diff = Diff(unverifiable=[ProviderResult("groq", None, "none", "403 forbidden")])

    report = render_report(diff, TODAY, {})

    assert "groq" in report
    assert "403 forbidden" in report


def test_report_on_clean_run_says_so():
    report = render_report(Diff(), TODAY, {"openai": "scrape"})

    assert "no drift" in report.lower()


def test_file_sink_writes_latest_and_dated_copy(tmp_path):
    paths = write_file_sink("# report body", tmp_path, TODAY)

    latest = tmp_path / "latest.md"
    dated = tmp_path / "history" / "2026-07-29.md"

    assert set(paths) == {latest, dated}
    assert latest.read_text(encoding="utf-8") == "# report body"
    assert dated.read_text(encoding="utf-8") == "# report body"


def test_file_sink_overwrites_latest_on_rerun(tmp_path):
    write_file_sink("first", tmp_path, TODAY)
    write_file_sink("second", tmp_path, TODAY)

    assert (tmp_path / "latest.md").read_text(encoding="utf-8") == "second"


def test_file_sink_creates_missing_directories(tmp_path):
    root = tmp_path / "does" / "not" / "exist"

    write_file_sink("body", root, TODAY)

    assert (root / "latest.md").exists()


def test_report_lists_entries_overdue_for_review():
    diff = Diff(stale=[("openai", "gpt-5.5", "never")])

    report = render_report(diff, TODAY, {})

    assert "gpt-5.5" in report
    assert "never" in report
    assert "overdue" in report.lower()


def test_run_returns_zero_and_writes_report_on_clean_catalog(tmp_path, monkeypatch):
    from src.catalog_check.__main__ import run

    monkeypatch.setattr(
        "src.catalog_check.__main__.SCRAPE_CONFIGS",
        [ScrapeConfig("openai", "https://example.test/o", r"gpt-[0-9.]+", 1)],
    )
    monkeypatch.setattr(
        "src.catalog_check.__main__.MODELS",
        [{"provider": "openai", "id": "gpt-5.5", "label": "GPT-5.5",
          "verified_at": "2026-07-01"}],
    )

    code = run(tmp_path, TODAY, fetch=lambda url: "gpt-5.5")

    assert code == 0
    assert (tmp_path / "latest.md").exists()


def test_run_returns_nonzero_on_unknown_id(tmp_path, monkeypatch):
    from src.catalog_check.__main__ import run

    monkeypatch.setattr(
        "src.catalog_check.__main__.SCRAPE_CONFIGS",
        [ScrapeConfig("openai", "https://example.test/o", r"gpt-[0-9.]+", 1)],
    )
    monkeypatch.setattr(
        "src.catalog_check.__main__.MODELS",
        [{"provider": "openai", "id": "gpt-old", "label": "old",
          "verified_at": "2026-07-01"}],
    )

    code = run(tmp_path, TODAY, fetch=lambda url: "gpt-5.5")

    assert code == 1
    assert "gpt-old" in (tmp_path / "latest.md").read_text(encoding="utf-8")


def test_run_still_writes_report_when_every_provider_fails(tmp_path, monkeypatch):
    from src.catalog_check.__main__ import run

    monkeypatch.setattr(
        "src.catalog_check.__main__.SCRAPE_CONFIGS",
        [ScrapeConfig("openai", "https://example.test/o", r"gpt-[0-9.]+", 1)],
    )
    monkeypatch.setattr(
        "src.catalog_check.__main__.MODELS",
        [{"provider": "openai", "id": "gpt-5.5", "label": "GPT-5.5",
          "verified_at": "2026-07-01"}],
    )

    def boom(url):
        raise RuntimeError("no network")

    code = run(tmp_path, TODAY, fetch=boom)

    report = (tmp_path / "latest.md").read_text(encoding="utf-8")
    assert code == 0  # unverifiable alone is not blocking
    assert "Unverifiable" in report
    assert "no network" in report
