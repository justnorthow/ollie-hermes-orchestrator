"""Who a given agent may talk to, and which of them can be consulted inline.

Consult eligibility is derived from the model's `speed_class` in src/catalog.py
rather than a separate list, so the weekly catalog-freshness check keeps it from
going stale. A model absent from the catalog has no verified speed class and is
therefore not consult-eligible — fail closed.

There is exactly ONE gate on who may be consulted: `consult_eligible`, which is
a latency and cost boundary — a `heavy` peer would block the caller's turn.

The human's own agent-access tier (`scope` / `manager_visible`) deliberately
does NOT narrow this roster. Those fields govern which agents a *human* sees in
their picker, so nobody has to remember which agent does what; they are not a
limit on which peers an agent may consult. `beyond_human_reach()` below records
when a consult crossed that line without preventing it.
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


def beyond_human_reach(peer: Teammate, tier: str, can_reach) -> bool:
    """True when the human at `tier` could not have opened `peer` directly.

    This ANNOTATES a consult; it does not block one. `scope` and
    `manager_visible` describe how *humans* reach agents — they keep the
    picker uncluttered so nobody has to remember which agent does what. They
    are deliberately NOT a limit on which peers an agent may consult: a chief
    of staff reaches the whole bench, which is the point of having one.

    An earlier version filtered the roster by this predicate, on the reading
    that scope was an authority boundary. It is not, and the filter made the
    concierge less useful than the humans it serves. What survives is the
    record: consults are read-only, so the exposure here is information a
    human could have asked their agent to find anyway — but "did anyone use
    the chief of staff to see something they could not see directly?" should
    stay an answerable question, so the crossing is stamped on the audit row.

    `can_reach(tier, scope, manager_visible)` is INJECTED rather than imported
    so this module stays pure: src/api/authz.py owns the rule and pulls in
    src/api/roles.py (httpx, Supabase) transitively. Injection keeps one
    definition of the rule while leaving src/dispatch/ free of src/api/.
    """
    return not can_reach(tier, peer.scope, peer.manager_visible)
