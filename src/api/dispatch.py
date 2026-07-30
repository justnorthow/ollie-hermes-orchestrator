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
    # read_agents() requires a Path (it calls .read_text() on its argument), matching
    # every other call site in this codebase (e.g. src/api/main.py:
    # `read_agents(cfg.hermes_stack_dir / ".env")`). The brief's original
    # `os.path.join(...)` returns a plain str, which raises
    # AttributeError: 'str' object has no attribute 'read_text' — confirmed by running
    # this task's tests. Wrapping in Path() is the minimal fix that matches
    # read_agents' actual contract.
    return Path(os.environ.get("HERMES_STACK_DIR", "")) / ".env"


def port_for(agent: str) -> int | None:
    for entry in read_agents(_env_path(None)):
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


def _roster_for(agent: str):
    return build_roster(read_agents(_env_path(None)), MODELS, self_agent=agent)


@router.get("/v1/dispatch/teammates")
def teammates(agent: str):
    roster = _roster_for(agent)
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
        instance_id = request.app.state.config.instance_id
    except Exception:
        instance_id = None

    origin = resolve_origin(
        req,
        owner_lookup=get_session_owner,
        tier_lookup=resolve_tier,
        instance_id=instance_id or "",
    )
    if origin is None:
        # Fail closed. No provenance, no dispatch, and no backend is consulted.
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

    refusal = check(req, _roster_for(req.from_agent), origin, caps=Caps())
    if refusal is not None:
        record_consult(req, refusal, origin, instance_id, post=_audit_post)
        return refusal.__dict__

    port = port_for(req.to_agent)
    if port is None:
        refusal = ConsultResult.refused(
            REASON_FORBIDDEN, "peer has no gateway port", peer=req.to_agent
        )
        record_consult(req, refusal, origin, instance_id, post=_audit_post)
        return refusal.__dict__

    result = backend_for(mode)(req, port, _gateway_key(), post=_post,
                               timeout=_GATEWAY_TIMEOUT)
    record_consult(req, result, origin, instance_id, post=_audit_post)
    return result.__dict__
