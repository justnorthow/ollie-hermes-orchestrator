import base64
import json
import os
import re

import jwt
from jwt import PyJWKClient
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from src.auth import require_bearer

router = APIRouter(tags=["auth"], dependencies=[Depends(require_bearer)])

_AUTH_TOKEN_RE = re.compile(r"-auth-token(\.\d+)?$")
_CHUNK_SUFFIX_RE = re.compile(r"\.(\d+)$")

# Module-level memoized JWKS client singleton.
# Keyed on the JWKS URI so that if the URL changes between calls (e.g. in tests)
# a new client is constructed.
_jwks_client_cache: dict[str, PyJWKClient] = {}


def _get_jwks_client() -> PyJWKClient | None:
    """Return a memoized PyJWKClient for the configured SUPABASE_URL, or None if unset."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    if not url:
        return None
    jwks_uri = f"{url}/auth/v1/.well-known/jwks.json"
    if jwks_uri not in _jwks_client_cache:
        _jwks_client_cache[jwks_uri] = PyJWKClient(jwks_uri)
    return _jwks_client_cache[jwks_uri]


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
    access_token = _access_token_from_cookie(
        _reassemble_supabase_cookie(dict(request.cookies))
    )
    if not access_token:
        return Response(status_code=401)

    # Read the algorithm from the unverified header ONLY to select the right
    # verification key — each branch verifies with its own key type.
    # An unknown or missing alg is rejected immediately (includes "none").
    try:
        header = jwt.get_unverified_header(access_token)
    except jwt.PyJWTError:
        return Response(status_code=401)

    alg = header.get("alg", "")

    if alg in ("ES256", "RS256"):
        # Asymmetric path: verify via Supabase JWKS endpoint.
        # Routing on the unverified alg is safe because we only accept the
        # public key matched by `kid` from the JWKS — the shared HS256 secret
        # is never used here, so an attacker cannot substitute keys.
        client = _get_jwks_client()
        if client is None:
            return JSONResponse({"detail": "Supabase JWKS not configured (SUPABASE_URL unset)"}, status_code=503)
        try:
            signing_key = client.get_signing_key_from_jwt(access_token)
            claims = jwt.decode(
                access_token,
                signing_key.key,
                algorithms=[alg],
                audience="authenticated",
            )
        except jwt.PyJWTError:
            return Response(status_code=401)
        except Exception:
            # JWKS fetch / network failure: deny rather than 500
            return Response(status_code=401)

    elif alg == "HS256":
        # Symmetric path: verify with the shared secret.
        secret = os.environ.get("SUPABASE_JWT_SECRET", "").strip()
        if not secret:
            return JSONResponse({"detail": "Supabase auth not configured"}, status_code=503)
        try:
            claims = jwt.decode(access_token, secret, algorithms=["HS256"], audience="authenticated")
        except jwt.PyJWTError:
            return Response(status_code=401)

    else:
        # Unknown or disallowed algorithm (including "none") → reject.
        return Response(status_code=401)

    email = claims.get("email")
    if not email:
        return Response(status_code=401)
    role = claims.get("user_role") or "agent"
    return Response(status_code=200, headers={"X-Auth-Email": email, "X-Auth-Role": role})
