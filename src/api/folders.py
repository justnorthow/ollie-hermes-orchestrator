from fastapi import APIRouter, Depends, Request

from src.api import authz
from src.auth import require_bearer
from src.folders_store import read_folders, write_folders
from src.models import FoldersPayload

router = APIRouter(
    prefix="/v1/folders",
    tags=["folders"],
    dependencies=[Depends(require_bearer)],
)


@router.get("")
async def get_folders(request: Request) -> dict:
    cfg = request.app.state.config
    return {"folders": read_folders(cfg)}


@router.put("")
async def put_folders(body: FoldersPayload, request: Request) -> dict:
    denied = authz.admin_denied(request)
    if denied:
        return denied
    cfg = request.app.state.config
    folders = [f.model_dump() for f in body.folders]
    write_folders(cfg, folders)
    return {"folders": folders}
