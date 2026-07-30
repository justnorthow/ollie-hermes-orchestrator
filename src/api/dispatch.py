"""Dispatch API — the mediator between a calling agent and its peer.

The plugin never reaches a peer gateway; it calls here. That is what makes
provenance resolvable rather than asserted: this module derives the human from
`agent_sessions` and the caller has no way to influence the answer.
"""
import logging
import os
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from src.agents_json import read_agents
from src.api.roles import resolve_tier
from src.api.sessions import get_session_owner
from src.auth import require_bearer
from src.catalog import MODELS
from src.dispatch.audit import record_consult
from src.dispatch.authority import Caps, check, resolve_origin
from src.dispatch.backends import backend_for
from src.dispatch.roster import build_roster
from src.dispatch.types import (
    MODE_OFF,
    REASON_FORBIDDEN,
    REASON_NOT_ENABLED,
    VALID_MODES,
    ConsultRequest,
    ConsultResult,
)

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["dispatch"], dependencies=[Depends(require_bearer)])

_GATEWAY_TIMEOUT = 30.0


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
    httpx.post(url, headers=headers, json=json, timeout=10.0).raise_for_status()


def _roster_for(agent: str, entries: list):
    return build_roster(entries, MODELS, self_agent=agent)


@router.get("/v1/dispatch/teammates")
def teammates(agent: str, request: Request):
    roster = _roster_for(agent, _agents(request.app.state.config))
    return {
        "mode": current_mode(),
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
        chain=tuple(body.chain),
    )

    try:
        cfg = request.app.state.config
        instance_id = cfg.instance_id
    except Exception:
        cfg = None
        instance_id = None

    origin = resolve_origin(
        req,
        owner_lookup=get_session_owner,
        tier_lookup=resolve_tier,
        instance_id=instance_id or "",
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
        return ConsultResult.refused(
            REASON_FORBIDDEN,
            "could not establish which human this request acts for",
            peer=req.to_agent,
        ).__dict__

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
        return refusal.__dict__

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
        return refusal.__dict__

    # One read of AGENTS_JSON, shared by the roster check and the port lookup below
    # (previously read and JSON-parsed twice per request).
    entries = _agents(cfg)

    refusal = check(req, _roster_for(req.from_agent, entries), origin, caps=Caps())
    if refusal is not None:
        record_consult(req, refusal, origin, instance_id, post=_audit_post)
        return refusal.__dict__

    port = port_for(req.to_agent, entries)
    if port is None:
        refusal = ConsultResult.refused(
            REASON_FORBIDDEN, "peer has no gateway port", peer=req.to_agent
        )
        record_consult(req, refusal, origin, instance_id, post=_audit_post)
        return refusal.__dict__

    result = driver(req, port, _gateway_key(), post=_post, timeout=_GATEWAY_TIMEOUT)
    record_consult(req, result, origin, instance_id, post=_audit_post)
    return result.__dict__
