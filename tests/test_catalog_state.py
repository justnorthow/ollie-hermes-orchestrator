from src.catalog_check.state import escalations, load_state, save_state, update_state
from src.catalog_check.types import Diff, ProviderResult


def _unverifiable(*providers):
    return Diff(unverifiable=[ProviderResult(p, None, "none", "x") for p in providers])


def test_load_state_missing_file_returns_empty(tmp_path):
    assert load_state(tmp_path / "nope.json") == {}


def test_load_state_corrupt_file_returns_empty(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("not json", encoding="utf-8")

    assert load_state(path) == {}


def test_first_unverifiable_run_counts_one():
    state = update_state({}, _unverifiable("groq"))

    assert state == {"groq": 1}
    assert escalations(state) == []


def test_second_consecutive_unverifiable_run_escalates():
    state = update_state({"groq": 1}, _unverifiable("groq"))

    assert state == {"groq": 2}
    assert escalations(state) == ["groq"]


def test_successful_run_clears_the_counter():
    state = update_state({"groq": 5}, Diff())

    assert state == {}
    assert escalations(state) == []


def test_one_provider_recovering_does_not_clear_another():
    state = update_state({"groq": 1, "openai": 1}, _unverifiable("openai"))

    assert state == {"openai": 2}
    assert escalations(state) == ["openai"]


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "nested" / "state.json"

    save_state(path, {"groq": 2})

    assert load_state(path) == {"groq": 2}
