"""Who a given agent may talk to, and which of them can be consulted inline.

Consult eligibility is derived from the model's `speed_class` in src/catalog.py
rather than a separate list, so the weekly catalog-freshness check keeps it from
going stale. A model absent from the catalog has no verified speed class and is
therefore not consult-eligible — fail closed.

Two independent gates, in order: `visible_to()` decides whether the originating
human may see the peer at all (their existing agent-access tier), and
`consult_eligible` decides whether a visible peer can be asked inline. The first
is a security boundary; the second is a latency/cost one.
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
                # Fail-closed getattr defaults, matching Teammate's own: an
                # entry that somehow lacks these is company-scope and not
                # manager-visible, i.e. reachable only by account_admin+.
                scope=getattr(entry, "scope", "company"),
                manager_visible=bool(getattr(entry, "manager_visible", False)),
            )
        )
    return sorted(roster, key=lambda t: t.agent_id)


def visible_to(roster: list[Teammate], tier: str, can_reach) -> list[Teammate]:
    """The subset of `roster` the human at `tier` is already allowed to reach.

    `can_reach(tier, scope, manager_visible)` is INJECTED rather than imported
    so this module stays pure: src/api/authz.py owns the rule, and it pulls in
    src/api/roles.py (httpx, Supabase) transitively. Injection keeps one
    definition of the rule while leaving src/dispatch/ free of src/api/.

    This is the property that stops dispatch being a lateral path around the
    orchestrator's human->agent access control. An agent's authority is its
    human's authority: a peer the human cannot reach directly is not on the
    roster the calling agent sees, so authority.check() refuses it as
    `unknown_peer` -- the same answer an agent that does not exist gets, which
    is what keeps a refusal from confirming that it does.
    """
    return [t for t in roster if can_reach(tier, t.scope, t.manager_visible)]
