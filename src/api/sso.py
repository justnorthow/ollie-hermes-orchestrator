import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from src.auth import require_bearer
from src.sso import mint_hia_token, mint_sso_token

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
    # jti is a random single-use nonce; single-use enforcement lives in HIA's
    # verifier which records used jti values in the `sso_used_tokens` table.
    # The orchestrator only mints — this is by design, not a missing feature.
    token = mint_hia_token(email, secret)
    return JSONResponse(
        content={"token": token},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/v1/sso/app-token")
def app_token(request: Request):
    secret = os.environ.get("HIA_SSO_SECRET", "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="App SSO not configured")
    email = request.headers.get("X-Auth-Email", "").strip()
    if not email:
        raise HTTPException(status_code=401, detail="No authenticated user")
    role = request.headers.get("X-Auth-Role", "").strip() or "agent"
    token = mint_sso_token(email, role, secret)
    return JSONResponse(content={"token": token}, headers={"Cache-Control": "no-store"})
