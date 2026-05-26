from fastapi import APIRouter, Depends
from src.auth import require_bearer
from src.catalog import list_models, list_skills


router = APIRouter(prefix="/v1", tags=["catalog"], dependencies=[Depends(require_bearer)])


@router.get("/models")
async def models() -> dict:
    return {"models": list_models()}


@router.get("/skills")
async def skills() -> dict:
    return {"skills": list_skills()}
