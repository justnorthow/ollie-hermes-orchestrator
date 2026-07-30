from datetime import date

import httpx
import pytest

from src.catalog_check.providers import ScrapeConfig
from src.catalog_check.sinks import (
    LinearConfig,
    _LINEAR_TIMEOUT,
    make_linear_post,
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


def test_report_caps_full_checklists_and_lists_remainder_as_bullets():
    # An over-matching scrape pattern (or a genuine multi-launch week) must
    # not turn the report into dozens of 11-item checklists nobody reads.
    new = [("anthropic", f"claude-candidate-{i}") for i in range(7)]
    diff = Diff(new=new)

    report = render_report(diff, TODAY, {})

    # First 5 get the full checklist (a heading + the adoption items).
    for provider, model_id in new[:5]:
        assert f"### `{provider}` / `{model_id}`" in report
    # The remaining 2 are one-line bullets under the triage heading, not
    # full checklists.
    assert "### Further candidates needing triage" in report
    for provider, model_id in new[5:]:
        assert f"- `{provider}` / `{model_id}`" in report
        assert f"### `{provider}` / `{model_id}`" not in report
    # The total count is stated somewhere in the report.
    assert "7" in report


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


def test_run_still_raises_on_an_internal_error(tmp_path, monkeypatch):
    """run()'s own contract is unchanged by the exit-code-2 mapping added to
    main(): run() itself still propagates an unexpected exception rather
    than swallowing it. Only main() is responsible for turning that into
    exit code 2."""
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
    monkeypatch.setattr(
        "src.catalog_check.__main__.render_report",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rendering exploded")),
    )

    with pytest.raises(RuntimeError, match="rendering exploded"):
        run(tmp_path, TODAY, fetch=lambda url: "gpt-5.5")


def test_main_maps_an_internal_error_to_exit_code_2(tmp_path, monkeypatch):
    """main() must distinguish exit 1 (blocking findings) from exit 2 (the
    check itself crashed) — otherwise the GitHub Actions gate reports a
    false 'unknown model ids' message for what was actually a bug."""
    import sys

    import src.catalog_check.__main__ as main_mod

    monkeypatch.setattr(
        main_mod, "SCRAPE_CONFIGS",
        [ScrapeConfig("openai", "https://example.test/o", r"gpt-[0-9.]+", 1)],
    )
    monkeypatch.setattr(
        main_mod, "MODELS",
        [{"provider": "openai", "id": "gpt-5.5", "label": "GPT-5.5",
          "verified_at": "2026-07-01"}],
    )
    # Force an internal error without touching the network: http_fetch is
    # never reached because render_report raises first.
    monkeypatch.setattr(main_mod, "http_fetch", lambda url: "gpt-5.5")
    monkeypatch.setattr(
        main_mod, "render_report",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rendering exploded")),
    )
    monkeypatch.setattr(sys, "argv", ["catalog_check", "--root", str(tmp_path)])

    with pytest.raises(SystemExit) as exc_info:
        main_mod.main()

    assert exc_info.value.code == 2


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


def test_linear_sink_not_posted_for_permanent_stale_only_diff():
    """`stale` entries (e.g. verified_at="never", which is permanent-by-design
    for at least one catalog entry) must never open a Linear issue on their
    own — only an `unknown` id (has_blocking_findings) does. Before this
    fix, gating on `not diff.is_empty` meant a stale-only diff (which is
    never empty) posted an identical issue every single run, forever."""
    calls = []

    def post(url, payload):
        calls.append(payload)
        return {"data": {"issueCreate": {"issue": {"identifier": "JNO-1"}}}}

    config = LinearConfig(api_key="k", team_id="t")
    diff = Diff(stale=[("openai", "gpt-5.5", "never")])

    result = write_linear_sink("body", diff, config, post=post)

    assert result is None
    assert calls == []


def test_linear_sink_posts_on_drift():
    def post(url, payload):
        return {"data": {"issueCreate": {"issue": {"identifier": "JNO-42"}}}}

    config = LinearConfig(api_key="k", team_id="t")
    diff = Diff(unknown=[("openai", "gpt-old")])

    assert write_linear_sink("body", diff, config, post=post) == "JNO-42"


def test_linear_sink_failure_surfaces_cause_and_does_not_raise():
    def post(url, payload):
        raise RuntimeError("502 bad gateway")

    config = LinearConfig(api_key="k", team_id="t")
    diff = Diff(unknown=[("openai", "gpt-old")])

    result = write_linear_sink("body", diff, config, post=post)

    assert result == "failed: RuntimeError: 502 bad gateway"


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
    assert statuses["linear"] == "failed: RuntimeError: 502"


def test_run_sinks_reports_linear_as_skipped_when_unconfigured(tmp_path):
    statuses = run_sinks(
        "body", Diff(), tmp_path, TODAY,
        LinearConfig(api_key=None, team_id=None),
        post=lambda url, payload: {},
    )

    assert statuses["file"] == "written"
    assert statuses["linear"] == "skipped (not configured)"


def test_run_sinks_reports_linear_as_skipped_when_no_blocking_findings(tmp_path):
    """Configured Linear, but a stale-only diff (never empty, never
    blocking) must report the accurate status, not the old misleading
    "skipped (no drift)" wording — there IS drift (a stale entry), it just
    is not blocking."""
    calls = []

    def post(url, payload):
        calls.append(payload)
        return {"data": {"issueCreate": {"issue": {"identifier": "JNO-1"}}}}

    statuses = run_sinks(
        "body",
        Diff(stale=[("openai", "gpt-5.5", "never")]),
        tmp_path,
        TODAY,
        LinearConfig(api_key="k", team_id="t"),
        post=post,
    )

    assert statuses["file"] == "written"
    assert statuses["linear"] == "skipped (no blocking findings)"
    assert calls == []


def test_linear_config_from_env_reads_both_vars(monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    monkeypatch.setenv("LINEAR_TEAM_ID", "t")

    config = LinearConfig.from_env()

    assert config.configured is True


def test_linear_config_partial_env_is_not_configured(monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    monkeypatch.delenv("LINEAR_TEAM_ID", raising=False)

    assert LinearConfig.from_env().configured is False


# --- make_linear_post transport contract -----------------------------------
#
# This is the test that would have caught the original bug: LinearConfig.
# api_key was read from the environment, used only to compute `configured`,
# and then never reached the outgoing request — Linear's GraphQL endpoint
# rejects unauthenticated requests, so every configured run failed silently.
# httpx.MockTransport lets us assert on the real request the transport
# builds, offline, with no new dependency (it ships with httpx).


def test_make_linear_post_sends_raw_unprefixed_authorization_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["content_type"] = request.headers.get("content-type")
        return httpx.Response(
            200, json={"data": {"issueCreate": {"issue": {"identifier": "JNO-1"}}}}
        )

    post = make_linear_post("lin_api_secret", transport=httpx.MockTransport(handler))
    result = post("https://api.linear.app/graphql", {"query": "..."})

    # Linear personal API keys go in Authorization RAW — no "Bearer " prefix.
    assert seen["authorization"] == "lin_api_secret"
    assert seen["content_type"] == "application/json"
    assert result["data"]["issueCreate"]["issue"]["identifier"] == "JNO-1"


def test_make_linear_post_sets_timeout():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions["timeout"]
        return httpx.Response(200, json={"data": {}})

    post = make_linear_post("k", transport=httpx.MockTransport(handler))
    post("https://api.linear.app/graphql", {})

    assert seen["timeout"]["connect"] == _LINEAR_TIMEOUT


def test_make_linear_post_raises_on_non_2xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    post = make_linear_post("bad-key", transport=httpx.MockTransport(handler))

    with pytest.raises(httpx.HTTPStatusError):
        post("https://api.linear.app/graphql", {"query": "..."})


# --- http_fetch transport contract ------------------------------------------


def test_http_fetch_returns_text_on_200():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="claude-opus-5 claude-sonnet-5")

    from src.catalog_check.providers import http_fetch

    body = http_fetch(
        "https://example.test/models", transport=httpx.MockTransport(handler)
    )

    assert "claude-opus-5" in body


def test_http_fetch_raises_on_500():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    from src.catalog_check.providers import http_fetch

    with pytest.raises(httpx.HTTPStatusError):
        http_fetch("https://example.test/models", transport=httpx.MockTransport(handler))
