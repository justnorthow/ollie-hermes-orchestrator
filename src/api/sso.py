import os

from fastapi import APIRouter, Depends, HTTPException

from src.auth import require_bearer
from src.sso import mint_hia_token

router = APIRouter(
    tags=["sso"],
    dependencies=[Depends(require_bearer)],
)


@router.get("/v1/sso/hia-token")
def hia_token():
    secret = os.environ.get("HIA_SSO_SECRET", "").strip()
    email = os.environ.get("HIA_BROKER_EMAIL", "").strip()
    if not secret or not email:
        raise HTTPException(status_code=503, detail="HIA SSO not configured")
    return {"token": mint_hia_token(email, secret)}
