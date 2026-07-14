import logging
import re
import time

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.agents_json import set_env_key
from src.api import authz
from src.audit import audit
from src.auth import require_bearer
from src.docker_ops import bounce_dashboard
from src.lock import async_file_lock

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/instance", tags=["instance"], dependencies=[Depends(require_bearer)])

MAX_TITLE_LEN = 80
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class SetTitle(BaseModel):
    title: str


def _bounce_after_write(cfg, actor_ip: str, title: str) -> None:
    """Runs as a BackgroundTask after the response has been sent, so bouncing
    the dashboard container (which houses the nginx that proxied this very
    request) can't sever the in-flight response. See lifecycle.py's
    create_agent deferred-bounce comment for the same trap. Must never raise:
    a raising background task poisons the request in tests and logs."""
    try:
        bounce_dashboard()
    except Exception as e:
        _logger.warning("instance title: deferred dashboard bounce failed", exc_info=True)
        audit(cfg.audit_log_path, op="set_instance_title", agent_id="-", actor_ip=actor_ip,
              result="error", duration_ms=0, error=f"deferred bounce failed: {e}")


@router.put("/title")
async def set_title(body: SetTitle, request: Request, background_tasks: BackgroundTasks):
    """Persist the instance display title: INSTANCE_TITLE in the stack .env,
    then bounce the dashboard so generate-config.sh re-emits config.js.
    Empty title clears (key stays present, blank value).

    The dashboard bounce is deferred until after the response is sent (via
    BackgroundTasks): the browser's PUT reaches us through the dashboard
    container's own nginx, so bouncing before responding would kill the
    in-flight proxied response even though the save succeeded."""
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
    async with async_file_lock(cfg.hermes_stack_dir / ".agents.lock"):
        set_env_key(cfg.hermes_stack_dir / ".env", "INSTANCE_TITLE", title)

    actor_ip = request.client.host if request.client else "unknown"
    audit(cfg.audit_log_path, op="set_instance_title", agent_id="-", actor_ip=actor_ip,
          result="ok", duration_ms=int((time.monotonic() - started) * 1000),
          title=title, bounce="deferred")
    background_tasks.add_task(_bounce_after_write, cfg, actor_ip, title)
    return {"ok": True}
