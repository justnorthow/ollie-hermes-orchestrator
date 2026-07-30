"""Dispatch API — the mediator between a calling agent and its peer.

The plugin never reaches a peer gateway; it calls here. That is what makes
provenance resolvable rather than asserted: this module derives the human from
`agent_sessions` and the caller has no way to influence the answer.

This module is also the only place the pure logic in src/dispatch/ is wired to
the orchestrator's own rules — `can_reach` from src/api/authz.py (whose peers a
human may see at all) and the process-wide in-flight registry (how deep a
consult chain may go). Both are injected rather than imported by src/dispatch/,
which keeps that package free of src/api/ and of network-capable modules.
"""
import logging
import os
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from src.agents_json import read_agents
from src.api.authz import can_reach
from src.api.roles import resolve_tier
from src.api.sessions import get_session_owner
from src.auth import require_bearer
from src.catalog import MODELS
from src.dispatch.audit import record_consult
from src.dispatch.authority import Caps, check, resolve_origin
from src.dispatch.backends import backend_for
from src.dispatch.inflight import InFlight
from src.dispatch.roster import build_roster, visible_to
from src.dispatch.types import (
    MODE_OFF,
    REASON_CAP_EXCEEDED,
    REASON_FORBIDDEN,
    REASON_MISCONFIGURED,
    REASON_NOT_ENABLED,
    VALID_MODES,
    ConsultRequest,
    ConsultResult,
)

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["dispatch"], dependencies=[Depends(require_bearer)])

# The server's worst-case budget for one consult, leg by leg. The plugin's
# client timeout (plugins/dispatch/http_client.py) must exceed the total, or it
# gives up while this process is still working and the audit trail records a
# granted consult that nobody ever received.
_OWNER_LOOKUP_TIMEOUT = 10.0  # get_session_owner, src/api/sessions.py
_TIER_LOOKUP_TIMEOUT = 10.0   # resolve_tier, src/api/roles.py
_GATEWAY_TIMEOUT = 30.0       # the peer's own generation
_AUDIT_TIMEOUT = 10.0         # the governance_events write below
#: Not a setting — the sum of the four legs above, exported so the client's
#: budget can be asserted against it (tests/test_dispatch_timeout_budget.py).
#: The first two mirror timeouts owned by sessions.py and roles.py; if either
#: of those changes, change the mirror here.
SERVER_WORST_CASE_SECONDS = (
    _OWNER_LOOKUP_TIMEOUT + _TIER_LOOKUP_TIMEOUT + _GATEWAY_TIMEOUT + _AUDIT_TIMEOUT
)

_CAPS = Caps()

#: Process-wide. Bounds mutual recursion between agents, which `req.chain`
#: cannot: see src/dispatch/inflight.py for why the bound is server-side.
_INFLIGHT = InFlight(max_depth=_CAPS.hop_cap)

# `chain` is caller-asserted, unvalidated, and copied into an append-only audit
# table. It is not a security control (inflight.py is), so it is clamped rather
# than rejected — a malformed chain must not cost the human their answer.
_MAX_CHAIN_LINKS = 8
_MAX_CHAIN_LINK_CHARS = 64


class ConsultBody(BaseModel):
    from_agent: str
    session_id: str
    to_agent: str
    question: str
    chain: list[str] = []
    # Any identity fields a caller sends are deliberately absent from this model:
    # pydantic drops unknown keys, so a caller cannot assert who it acts for.


def current_mode() -> str:
    mode = os.environ.get("DISPATCH_MODE", MODE_OFF).strip() or MODE_OFF
    return mode if mode in VALID_MODES else MODE_OFF


def _clamp_chain(chain: list[str]) -> tuple[str, ...]:
    """Bound caller-supplied chain data before it can reach the audit table."""
    return tuple(str(link)[:_MAX_CHAIN_LINK_CHARS]
                 for link in chain[:_MAX_CHAIN_LINKS])


def _env_path(cfg) -> Path:
    # Matches src/api/authz.py:28-29 (same name, same signature) and every other
    # read_agents call site in this codebase. cfg.hermes_stack_dir is resolved once
    # by Config.load() and already defaults to $HOME/hermes-stack when
    # HERMES_STACK_DIR is unset (src/config.py:27) -- HERMES_STACK_DIR is a
    # documented-optional override (.env.example), not a required var. Reading
    # os.environ["HERMES_STACK_DIR"] directly here (the brief's original code) skips
    # that default: on a default install the var is unset, and dispatch would look
    # for .env at a relative, nonexistent path instead of the real stack dir.
    return cfg.hermes_stack_dir / ".env"


def _agents(cfg) -> list:
    return read_agents(_env_path(cfg))


def port_for(agent: str, entries: list) -> int | None:
    for entry in entries:
        if entry.id == agent:
            return entry.gateway_port
    return None


def _gateway_key() -> str:
    return os.environ.get("HERMES_GATEWAY_KEY", "")


def _post(url, headers, json, timeout):
    resp = httpx.post(url, headers=headers, json=json, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _audit_post(url, headers, json):
    httpx.post(url, headers=headers, json=json,
               timeout=_AUDIT_TIMEOUT).raise_for_status()


def _config(request: Request):
    """app.state.config, or None when it cannot be read.

    request.app can raise on a bare scope — src/api/sessions.py::_rbac_denied
    and src/api/authz.py::admin_denied both guard it explicitly because it has
    happened. Unlike those two, dispatch fails CLOSED on it: they are gates that
    default to trusting an internal caller, this is the path that would
    otherwise reach a live model with an unattributed request.
    """
    try:
        return request.app.state.config
    except Exception:  # noqa: BLE001 — absence of config is refused, not raised
        _logger.warning("dispatch: app config unavailable", exc_info=True)
        return None


def _body(result: ConsultResult) -> dict:
    """Explicit wire shape.

    Returning `result.__dict__` would auto-publish any field a later change adds
    to ConsultResult, with nobody reviewing the fact that it became part of a
    public API response. Every field here is named on purpose.
    """
    return {
        "ok": result.ok,
        "answer": result.answer,
        "reason": result.reason,
        "detail": result.detail,
        "peer": result.peer,
    }


def _roster_for(agent: str, entries: list, tier: str):
    """Peers `agent` may consult, narrowed to what its human may already reach.

    The narrowing is the fix for dispatch being a lateral path around the
    orchestrator's human->agent access control: without it, a member-tier human
    whose dashboard hides karl-m (company scope, not manager-visible) could ask
    any agent to ask karl-m, and karl's answer would come back verbatim.
    """
    return visible_to(build_roster(entries, MODELS, self_agent=agent),
                      tier, can_reach)


@router.get("/v1/dispatch/teammates")
def teammates(agent: str, session_id: str, request: Request):
    """The roster this agent's human may see. Mode-gated and provenance-gated.

    `session_id` is required for the same reason `consult` needs one: the tier
    that filters this list has to come from a resolved human, never from the
    caller. Before that, this endpoint answered any holder of the shared
    ORCHESTRATOR_KEY — which is every profile on the box — with every agent's
    id, display name and subtitle, on a box where dispatch was switched off.
    """
    mode = current_mode()
    if mode == MODE_OFF:
        # Inert means inert: off mode enumerates nothing, in the same way it
        # consults nothing. Not even the count of peers leaks.
        return {"ok": False, "mode": mode, "teammates": [],
                "reason": REASON_NOT_ENABLED,
                "detail": "dispatch is disabled on this instance (DISPATCH_MODE=off)"}

    cfg = _config(request)
    if cfg is None:
        return {"ok": False, "mode": mode, "teammates": [],
                "reason": REASON_MISCONFIGURED,
                "detail": "orchestrator configuration is unavailable"}

    origin = resolve_origin(
        agent, session_id,
        owner_lookup=get_session_owner,
        tier_lookup=resolve_tier,
        instance_id=cfg.instance_id,
    )
    if origin is None:
        _logger.warning("dispatch: unresolvable provenance for teammates %s/%s",
                        agent, session_id)
        return {"ok": False, "mode": mode, "teammates": [],
                "reason": REASON_FORBIDDEN,
                "detail": "could not establish which human this request acts for"}

    roster = _roster_for(agent, _agents(cfg), origin.tier)
    return {
        # `ok` on the success shape too: the plugin returns this payload straight
        # to a model that has been told to distinguish refusals from answers, and
        # one tool that answers in two incompatible shapes is how that instruction
        # gets misread.
        "ok": True,
        "mode": mode,
        "teammates": [
            {
                "agent_id": t.agent_id,
                "display_name": t.display_name,
                "subtitle": t.subtitle,
                "speed_class": t.speed_class,
                "consult_eligible": t.consult_eligible,
            }
            for t in roster
        ],
    }


@router.post("/v1/dispatch/consult")
def consult(body: ConsultBody, request: Request):
    req = ConsultRequest(
        from_agent=body.from_agent,
        session_id=body.session_id,
        to_agent=body.to_agent,
        question=body.question,
        chain=_clamp_chain(body.chain),
    )

    cfg = _config(request)
    if cfg is None:
        # Fail closed, and structured: this used to fall through with cfg=None
        # into _agents(cfg) -> AttributeError -> an unhandled 500, which reaches
        # the calling model as an empty tool result. No audit row is possible
        # here (there is no Origin yet), so the log line is the only trace.
        return _body(ConsultResult.refused(
            REASON_MISCONFIGURED,
            "orchestrator configuration is unavailable",
            peer=req.to_agent,
        ))
    instance_id = cfg.instance_id

    origin = resolve_origin(
        req.from_agent, req.session_id,
        owner_lookup=get_session_owner,
        tier_lookup=resolve_tier,
        instance_id=instance_id,
    )
    if origin is None:
        # Fail closed. No provenance, no dispatch, and no backend is consulted.
        # This is the highest-signal security event on this endpoint (an agent
        # process enumerating session ids), so it must leave a trace even though
        # there is no origin yet to attribute a governance_events row to.
        _logger.warning(
            "dispatch: unresolvable provenance for %s/%s",
            req.from_agent, req.session_id,
        )
        return _body(ConsultResult.refused(
            REASON_FORBIDDEN,
            "could not establish which human this request acts for",
            peer=req.to_agent,
        ))

    # Mode is checked before any roster or port lookup, so no code path can
    # reach a gateway when dispatch is disabled. Task 8 asserts this.
    mode = current_mode()
    if mode == MODE_OFF:
        refusal = ConsultResult.refused(
            REASON_NOT_ENABLED,
            "dispatch is disabled on this instance (DISPATCH_MODE=off)",
            peer=req.to_agent,
        )
        record_consult(req, refusal, origin, instance_id, post=_audit_post)
        return _body(refusal)

    # A mode can be VALID (src/dispatch/types.py) without a backend driver existing
    # for it yet (local/linear in this slice — src/dispatch/backends.py only wires
    # off and direct). Resolving the driver here, before any roster or port lookup,
    # turns that into a structured refusal instead of an unhandled 500 reaching the
    # calling model as an empty tool result.
    try:
        driver = backend_for(mode)
    except ValueError:
        refusal = ConsultResult.refused(
            REASON_NOT_ENABLED,
            f"dispatch mode {mode!r} is not implemented on this instance",
            peer=req.to_agent,
        )
        record_consult(req, refusal, origin, instance_id, post=_audit_post)
        return _body(refusal)

    # One read of AGENTS_JSON, shared by the roster check and the port lookup below
    # (previously read and JSON-parsed twice per request).
    entries = _agents(cfg)

    refusal = check(req, _roster_for(req.from_agent, entries, origin.tier),
                    origin, caps=_CAPS)
    if refusal is not None:
        record_consult(req, refusal, origin, instance_id, post=_audit_post)
        return _body(refusal)

    port = port_for(req.to_agent, entries)
    if port is None:
        refusal = ConsultResult.refused(
            REASON_FORBIDDEN, "peer has no gateway port", peer=req.to_agent
        )
        record_consult(req, refusal, origin, instance_id, post=_audit_post)
        return _body(refusal)

    # The in-flight hold is the last gate, and it wraps the ONLY call in this
    # function that can block for tens of seconds. Everything above it is a
    # cheap check, so nothing cheap holds a slot. The `with` is what guarantees
    # release on timeout and on exception alike.
    with _INFLIGHT.hold(origin.user_id, req.to_agent) as denial:
        if denial is not None:
            refusal = ConsultResult.refused(
                REASON_CAP_EXCEEDED, denial, peer=req.to_agent
            )
            record_consult(req, refusal, origin, instance_id, post=_audit_post)
            return _body(refusal)

        result = driver(req, port, _gateway_key(), post=_post,
                        timeout=_GATEWAY_TIMEOUT)

    record_consult(req, result, origin, instance_id, post=_audit_post)
    return _body(result)
