import json
import logging
import time
from pathlib import Path
from typing import Any


_SECRET_KEYS = {"api_key", "apikey", "apiKey", "token", "key", "secret", "password"}
_logger = logging.getLogger(__name__)


def _redact(d: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in d.items():
        if k in _SECRET_KEYS:
            continue
        out[k] = v
    return out


def audit(
    log_path: Path,
    *,
    op: str,
    agent_id: str,
    actor_ip: str,
    result: str,
    duration_ms: int,
    error: str | None = None,
    **extras: Any,
) -> None:
    record: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "op": op,
        "agent_id": agent_id,
        "actor_ip": actor_ip,
        "result": result,
        "duration_ms": duration_ms,
    }
    if error:
        record["error"] = error
    record.update(_redact(extras))
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        _logger.warning("audit log write failed", exc_info=True)
