"""Dispatch's writes to the shared governance_events audit trail.

Wire format mirrors src/api/admin.py's existing writer so both produce rows the
same RLS policies and dashboard reader understand.

The answer text is deliberately NOT recorded. The trail's job is to prove who
asked whom what, under whose authority — storing arbitrary model output in an
append-only table nobody can redact is a liability, not an audit improvement.
"""
import logging
import os

from src.dispatch.authority import Origin
from src.dispatch.types import ConsultRequest, ConsultResult

_logger = logging.getLogger(__name__)


def record_consult(
    req: ConsultRequest,
    result: ConsultResult,
    origin: Origin,
    instance_id: str | None,
    post,
) -> None:
    """Append one dispatch_consult row. Best-effort — never raises."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not (url and key):
        return

    content = (
        f"{req.from_agent} -> {req.to_agent}: {req.question}"
        if result.ok
        else f"{req.from_agent} -> {req.to_agent} refused ({result.reason}): "
             f"{result.detail}"
    )

    try:
        post(
            f"{url}/rest/v1/governance_events",
            {
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            {
                "user_email": origin.user_id,
                "user_role": origin.tier,
                "app": "dispatch",
                "event_type": "dispatch_consult",
                "status": "ok" if result.ok else "flagged",
                "title": f"{req.from_agent} -> {req.to_agent}",
                "findings": [{"text": "chain", "chain": list(req.chain)}],
                "content": content,
                "run_id": None,
                "instance_id": instance_id,
            },
        )
    except Exception:
        _logger.warning("dispatch audit write failed", exc_info=True)
