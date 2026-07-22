"""Broker-uploaded MLS market datasets (Newsletter Studio MLS mode).

Parse and save are deliberately separate routes: the model produces a DRAFT the
browser shows for confirmation, and only user-confirmed values are ever stored.
All Supabase access is service-role PostgREST (mirrors market_data.py)."""
import logging
import os
import uuid

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.agents_json import read_agents
from src.api.roles import is_at_least, resolve_tier
from src.auth import require_bearer
from src.market_parse import (
    MAX_UPLOAD_BYTES, call_gateway_parse, prepare_user_content,
    validate_parse_output,
)

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["market-datasets"], dependencies=[Depends(require_bearer)])

_LIST_COLUMNS = ("id,label,linked_area,period_label,period_end,figures,"
                 "source_label,uploader_name,created_at")


def _creds() -> "tuple[str, str] | None":
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    return (url, key) if url and key else None


def _identity(request: Request) -> "tuple[str, str] | None":
    """(user_id, email) from the nginx-injected trusted headers, or None."""
    uid = request.headers.get("X-Auth-User-Id", "").strip()
    email = request.headers.get("X-Auth-Email", "").strip()
    return (uid, email) if uid and email else None


def _agent_entry(request: Request, agent_id: str):
    """Registry entry for agent_id (module-level so tests can monkeypatch)."""
    cfg = request.app.state.config
    entries = read_agents(cfg.hermes_stack_dir / ".env")
    return next((e for e in entries if e.id == agent_id), None)


def _fetch_uploader(ds_id: str, url: str, key: str) -> "str | None":
    resp = httpx.get(
        f"{url}/rest/v1/market_datasets",
        params={"id": f"eq.{ds_id}", "select": "uploaded_by"},
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        timeout=10.0,
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0]["uploaded_by"] if rows else None


@router.post("/v1/market-datasets/parse")
async def parse_upload(request: Request, filename: str = "", agent: str = "real-estate"):
    if not _identity(request):
        return JSONResponse({"detail": "No authenticated user"}, status_code=401)
    body = await request.body()
    if len(body) > MAX_UPLOAD_BYTES:
        return JSONResponse({"detail": "file too large (10 MB max)"}, status_code=413)
    if not body:
        return JSONResponse({"detail": "empty body"}, status_code=400)
    try:
        content = prepare_user_content(filename, body)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=415)

    entry = _agent_entry(request, agent)
    gateway_key = request.app.state.hermes_gateway_key
    if entry is None or not gateway_key:
        return JSONResponse({"detail": "parsing agent unavailable"}, status_code=503)

    last_err = "parse failed"
    for _ in range(2):                       # one retry on junk output
        try:
            text = call_gateway_parse(content, entry.gateway_port, gateway_key)
            return JSONResponse(content=validate_parse_output(text),
                                headers={"Cache-Control": "no-store"})
        except ValueError as exc:            # model returned non-JSON — retry
            last_err = str(exc)
        except (RuntimeError, httpx.HTTPError, KeyError):
            _logger.exception("market-dataset parse gateway call failed")
            return JSONResponse({"detail": "couldn't reach the parsing model"},
                                status_code=502)
    return JSONResponse({"detail": f"couldn't read this file ({last_err})"},
                        status_code=422)


class SaveDataset(BaseModel):
    label: str
    period_label: str
    period_end: "str | None" = None
    linked_area: "dict | None" = None
    figures: dict
    source_label: str = ""
    file_b64: "str | None" = None            # original upload, base64 (optional)
    file_name: "str | None" = None


@router.post("/v1/market-datasets")
def save_dataset(body: SaveDataset, request: Request):
    ident = _identity(request)
    if not ident:
        return JSONResponse({"detail": "No authenticated user"}, status_code=401)
    uid, email = ident
    creds = _creds()
    if not creds:
        return JSONResponse({"detail": "supabase not configured"}, status_code=503)
    url, key = creds

    ds_id = str(uuid.uuid4())
    file_path = None
    if body.file_b64 and body.file_name:
        import base64
        file_path = f"{ds_id}/{body.file_name}"
        try:
            resp = httpx.post(
                f"{url}/storage/v1/object/market-uploads/{file_path}",
                content=base64.b64decode(body.file_b64),
                headers={"apikey": key, "Authorization": f"Bearer {key}",
                         "Content-Type": "application/octet-stream", "x-upsert": "true"},
                timeout=30.0,
            )
            resp.raise_for_status()
        except (httpx.HTTPError, ValueError):
            _logger.warning("market-dataset file upload failed; saving without file",
                            exc_info=True)
            file_path = None                 # dataset still saves; provenance file is best-effort

    figures = {k: str(body.figures.get(k, "")).strip()
               for k in ("medianSoldPrice", "inventoryMonths", "daysOnMarket", "salesVolume")}
    row = {"id": ds_id, "label": body.label.strip(), "linked_area": body.linked_area,
           "period_label": body.period_label.strip(), "period_end": body.period_end,
           "figures": figures, "source_label": body.source_label.strip(),
           "file_path": file_path, "uploaded_by": uid, "uploader_name": email}
    try:
        resp = httpx.post(
            f"{url}/rest/v1/market_datasets",
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Content-Type": "application/json", "Prefer": "return=minimal"},
            json=row, timeout=10.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        _logger.exception("market-dataset insert failed")
        return JSONResponse({"detail": "upstream database error"}, status_code=502)
    return {"ok": True, "id": ds_id}


@router.get("/v1/market-datasets")
def list_datasets(request: Request):
    if not _identity(request):
        return JSONResponse({"detail": "No authenticated user"}, status_code=401)
    creds = _creds()
    if not creds:
        return JSONResponse({"detail": "supabase not configured"}, status_code=503)
    url, key = creds
    try:
        resp = httpx.get(
            f"{url}/rest/v1/market_datasets",
            params={"select": _LIST_COLUMNS,
                    "order": "period_end.desc.nullslast,created_at.desc"},
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=10.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        _logger.exception("market-dataset list failed")
        return JSONResponse({"detail": "upstream database error"}, status_code=502)
    return JSONResponse(content={"datasets": resp.json()},
                        headers={"Cache-Control": "no-store"})


@router.delete("/v1/market-datasets/{ds_id}")
def delete_dataset(ds_id: str, request: Request):
    ident = _identity(request)
    if not ident:
        return JSONResponse({"detail": "No authenticated user"}, status_code=401)
    uid, _ = ident
    creds = _creds()
    if not creds:
        return JSONResponse({"detail": "supabase not configured"}, status_code=503)
    url, key = creds
    try:
        uploader = _fetch_uploader(ds_id, url, key)
    except httpx.HTTPError:
        _logger.exception("market-dataset uploader lookup failed")
        return JSONResponse({"detail": "upstream database error"}, status_code=502)
    if uploader is None:
        return JSONResponse({"detail": "not found"}, status_code=404)
    instance_id = request.app.state.config.instance_id
    if uploader != uid and not is_at_least(resolve_tier(instance_id, uid), "manager"):
        return JSONResponse({"detail": "forbidden"}, status_code=403)
    try:
        resp = httpx.delete(
            f"{url}/rest/v1/market_datasets",
            params={"id": f"eq.{ds_id}"},
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=10.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        _logger.exception("market-dataset delete failed")
        return JSONResponse({"detail": "upstream database error"}, status_code=502)
    return {"ok": True}
