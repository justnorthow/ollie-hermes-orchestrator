from dataclasses import dataclass

from src.dispatch.roster import build_roster, speed_class_for


@dataclass
class _Entry:
    """Stands in for src.agents_json.AgentEntry — only the fields roster reads."""
    id: str
    name: str
    subtitle: str | None = None
    model: str | None = None


MODELS = [
    {"provider": "openai", "id": "gpt-5.6-terra", "label": "Terra", "speed_class": "fast"},
    {"provider": "openai", "id": "gpt-5.6-sol", "label": "Sol", "speed_class": "heavy"},
]


def test_speed_class_looked_up_from_catalog():
    assert speed_class_for("gpt-5.6-terra", MODELS) == "fast"
    assert speed_class_for("gpt-5.6-sol", MODELS) == "heavy"


def test_speed_class_unknown_model_is_none():
    assert speed_class_for("gpt-9.9", MODELS) is None
    assert speed_class_for(None, MODELS) is None


def test_fast_peer_is_consult_eligible():
    roster = build_roster([_Entry("karl-m", "Karl M", "Email", "gpt-5.6-terra")],
                          MODELS, self_agent="billie")

    assert [t.agent_id for t in roster] == ["karl-m"]
    assert roster[0].consult_eligible is True
    assert roster[0].display_name == "Karl M"


def test_heavy_peer_is_listed_but_not_consult_eligible():
    """Heavy peers stay visible — the agent should know they exist and that it
    cannot consult them inline — rather than being hidden."""
    roster = build_roster([_Entry("deep", "Deep", None, "gpt-5.6-sol")],
                          MODELS, self_agent="billie")

    assert roster[0].consult_eligible is False
    assert roster[0].speed_class == "heavy"


def test_unknown_model_is_not_consult_eligible():
    """Fail closed: a model absent from the catalog has no verified speed class."""
    roster = build_roster([_Entry("mystery", "Mystery", None, "gpt-9.9")],
                          MODELS, self_agent="billie")

    assert roster[0].consult_eligible is False


def test_self_is_excluded_from_the_roster():
    roster = build_roster(
        [_Entry("billie", "Billie", None, "gpt-5.6-terra"),
         _Entry("karl-m", "Karl M", None, "gpt-5.6-terra")],
        MODELS, self_agent="billie",
    )

    assert [t.agent_id for t in roster] == ["karl-m"]


def test_consult_classes_is_configurable():
    roster = build_roster([_Entry("deep", "Deep", None, "gpt-5.6-sol")],
                          MODELS, self_agent="billie",
                          consult_classes=frozenset({"fast", "heavy"}))

    assert roster[0].consult_eligible is True


def test_roster_is_sorted_by_agent_id():
    roster = build_roster(
        [_Entry("zed", "Zed", None, "gpt-5.6-terra"),
         _Entry("abe", "Abe", None, "gpt-5.6-terra")],
        MODELS, self_agent="billie",
    )

    assert [t.agent_id for t in roster] == ["abe", "zed"]
