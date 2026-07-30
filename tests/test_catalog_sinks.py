from datetime import date

import pytest

from src.catalog_check.providers import ScrapeConfig
from src.catalog_check.sinks import (
    LinearConfig,
    render_report,
    run_sinks,
    write_file_sink,
    write_linear_sink,
)
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


def test_run_appends_escalation_block_after_two_consecutive_unverifiable_runs(
    tmp_path, monkeypatch
):
    """Drives run() twice against the same tmp_path root, so state persists
    between calls. The provider is unverifiable both times, crossing the
    escalation threshold on the second run. Asserts on the written report
    file's contents, not on state internals."""
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

    run(tmp_path, TODAY, fetch=boom)
    code = run(tmp_path, TODAY, fetch=boom)

    report = (tmp_path / "latest.md").read_text(encoding="utf-8")
    assert code == 0  # unverifiable alone is not blocking
    assert "## Escalation — unverifiable two runs running" in report
    assert "`openai`" in report
    assert "\n\n## Escalation" in report  # blank line separates it, like every other section


def test_linear_sink_skipped_when_unconfigured():
    config = LinearConfig(api_key=None, team_id=None)

    result = write_linear_sink("body", Diff(), config, post=lambda url, payload: {})

    assert result is None


def test_linear_sink_not_called_on_clean_run():
    calls = []

    def post(url, payload):
        calls.append(payload)
        return {"data": {"issueCreate": {"issue": {"identifier": "JNO-1"}}}}

    config = LinearConfig(api_key="k", team_id="t")
    result = write_linear_sink("body", Diff(), config, post=post)

    assert result is None
    assert calls == []


def test_linear_sink_posts_on_drift():
    def post(url, payload):
        return {"data": {"issueCreate": {"issue": {"identifier": "JNO-42"}}}}

    config = LinearConfig(api_key="k", team_id="t")
    diff = Diff(unknown=[("openai", "gpt-old")])

    assert write_linear_sink("body", diff, config, post=post) == "JNO-42"


def test_linear_sink_failure_returns_none_and_does_not_raise():
    def post(url, payload):
        raise RuntimeError("502 bad gateway")

    config = LinearConfig(api_key="k", team_id="t")
    diff = Diff(unknown=[("openai", "gpt-old")])

    assert write_linear_sink("body", diff, config, post=post) is None


def test_run_sinks_writes_file_even_when_linear_raises(tmp_path):
    def post(url, payload):
        raise RuntimeError("502")

    statuses = run_sinks(
        "body",
        Diff(unknown=[("openai", "gpt-old")]),
        tmp_path,
        TODAY,
        LinearConfig(api_key="k", team_id="t"),
        post=post,
    )

    assert (tmp_path / "latest.md").read_text(encoding="utf-8") == "body"
    assert statuses["file"] == "written"
    assert statuses["linear"] == "failed"


def test_run_sinks_reports_linear_as_skipped_when_unconfigured(tmp_path):
    statuses = run_sinks(
        "body", Diff(), tmp_path, TODAY,
        LinearConfig(api_key=None, team_id=None),
        post=lambda url, payload: {},
    )

    assert statuses["file"] == "written"
    assert statuses["linear"] == "skipped (not configured)"


def test_linear_config_from_env_reads_both_vars(monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    monkeypatch.setenv("LINEAR_TEAM_ID", "t")

    config = LinearConfig.from_env()

    assert config.configured is True


def test_linear_config_partial_env_is_not_configured(monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    monkeypatch.delenv("LINEAR_TEAM_ID", raising=False)

    assert LinearConfig.from_env().configured is False
