import logging
import re
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.agents_json import set_env_key
from src.api import authz
from src.audit import audit
from src.auth import require_bearer
from src.docker_ops import bounce_dashboard

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/instance", tags=["instance"], dependencies=[Depends(require_bearer)])

MAX_TITLE_LEN = 80
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class SetTitle(BaseModel):
    title: str


@router.put("/title")
async def set_title(body: SetTitle, request: Request):
    """Persist the instance display title: INSTANCE_TITLE in the stack .env,
    then bounce the dashboard so generate-config.sh re-emits config.js.
    Empty title clears (key stays present, blank value)."""
    denied = authz.admin_denied(request)
    if denied:
        return denied
    title = body.title.strip()
    if len(title) > MAX_TITLE_LEN:
        return JSONResponse({"ok": False, "error": f"title too long (max {MAX_TITLE_LEN} chars)"}, status_code=400)
    if _CONTROL_RE.search(title):
        return JSONResponse({"ok": False, "error": "title must not contain control characters"}, status_code=400)

    cfg = request.app.state.config
    started = time.monotonic()
    set_env_key(cfg.hermes_stack_dir / ".env", "INSTANCE_TITLE", title)
    error = None
    try:
        bounce_dashboard()
    except Exception as e:
        _logger.warning("instance title: dashboard bounce failed", exc_info=True)
        error = f"saved, but dashboard bounce failed: {e}"

    actor_ip = request.client.host if request.client else "unknown"
    audit(cfg.audit_log_path, op="set_instance_title", agent_id="-", actor_ip=actor_ip,
          result="ok" if error is None else "error",
          duration_ms=int((time.monotonic() - started) * 1000),
          error=error, title=title)
    if error:
        return {"ok": False, "error": error}
    return {"ok": True}
