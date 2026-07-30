"""Who a given agent may talk to, and which of them can be consulted inline.

Consult eligibility is derived from the model's `speed_class` in src/catalog.py
rather than a separate list, so the weekly catalog-freshness check keeps it from
going stale. A model absent from the catalog has no verified speed class and is
therefore not consult-eligible — fail closed.
"""
from src.dispatch.types import Teammate

DEFAULT_CONSULT_CLASSES = frozenset({"fast"})


def speed_class_for(model: str | None, models: list[dict]) -> str | None:
    """Look up a model's speed_class in the catalog. None when unknown."""
    if not model:
        return None
    for entry in models:
        if entry.get("id") == model:
            return entry.get("speed_class")
    return None


def build_roster(
    entries: list,
    models: list[dict],
    self_agent: str,
    consult_classes: frozenset[str] = DEFAULT_CONSULT_CLASSES,
) -> list[Teammate]:
    """Every other agent on the box, with consult eligibility resolved.

    Heavy peers are listed rather than hidden: the agent should know they exist
    and that it cannot consult them inline, so it can name them to its human
    instead of silently pretending they don't exist.
    """
    roster: list[Teammate] = []
    for entry in entries:
        if entry.id == self_agent:
            continue
        model = getattr(entry, "model", None)
        speed = speed_class_for(model, models)
        roster.append(
            Teammate(
                agent_id=entry.id,
                display_name=getattr(entry, "name", entry.id),
                subtitle=getattr(entry, "subtitle", None),
                model=model,
                speed_class=speed,
                consult_eligible=speed in consult_classes,
            )
        )
    return sorted(roster, key=lambda t: t.agent_id)
