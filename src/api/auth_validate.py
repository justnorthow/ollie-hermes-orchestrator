import base64
import json
import os
import re

import jwt
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from src.auth import require_bearer

router = APIRouter(tags=["auth"], dependencies=[Depends(require_bearer)])

_AUTH_TOKEN_RE = re.compile(r"-auth-token(\.\d+)?$")
_CHUNK_SUFFIX_RE = re.compile(r"\.(\d+)$")


def _reassemble_supabase_cookie(cookies: dict[str, str]) -> str | None:
    """Rebuild the @supabase/ssr session cookie value (single or chunked .0/.1/…)."""
    relevant = {n: v for n, v in cookies.items() if _AUTH_TOKEN_RE.search(n)}
    if not relevant:
        return None
    chunk_names = sorted(
        (n for n in relevant if _CHUNK_SUFFIX_RE.search(n)),
        key=lambda n: int(_CHUNK_SUFFIX_RE.search(n).group(1)),
    )
    if chunk_names:
        return "".join(relevant[n] for n in chunk_names)
    base_names = [n for n in relevant if not _CHUNK_SUFFIX_RE.search(n)]
    return relevant[base_names[0]] if base_names else None


def _access_token_from_cookie(raw: str | None) -> str | None:
    """Extract the access_token JWT from a @supabase/ssr cookie value."""
    if not raw:
        return None
    if raw.startswith("base64-"):
        try:
            raw = base64.b64decode(raw[len("base64-"):] + "===").decode("utf-8")
        except Exception:
            return None
    try:
        session = json.loads(raw)
    except Exception:
        return None
    token = session.get("access_token")
    return token if isinstance(token, str) else None


@router.get("/v1/auth/validate")
def validate(request: Request) -> Response:
    secret = os.environ.get("SUPABASE_JWT_SECRET", "").strip()
    if not secret:
        return JSONResponse({"detail": "Supabase auth not configured"}, status_code=503)
    access_token = _access_token_from_cookie(
        _reassemble_supabase_cookie(dict(request.cookies))
    )
    if not access_token:
        return Response(status_code=401)
    try:
        claims = jwt.decode(access_token, secret, algorithms=["HS256"], audience="authenticated")
    except jwt.PyJWTError:
        return Response(status_code=401)
    email = claims.get("email")
    if not email:
        return Response(status_code=401)
    role = claims.get("user_role") or "agent"
    return Response(status_code=200, headers={"X-Auth-Email": email, "X-Auth-Role": role})
