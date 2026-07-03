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
_SUPABASE_HOST_RE = re.compile(r"^https?://([a-z0-9-]+)\.supabase\.co")

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
        # Fix #5: pin lifespan to 60 s so stale keys are refreshed promptly.
        _jwks_client_cache[jwks_uri] = PyJWKClient(jwks_uri, lifespan=60)
    return _jwks_client_cache[jwks_uri]


def _project_ref(supabase_url: str) -> str | None:
    """Extract the Supabase project ref from a supabase.co URL, or None for custom domains."""
    m = _SUPABASE_HOST_RE.match(supabase_url)
    return m.group(1) if m else None


def _reassemble_supabase_cookie(cookies: dict[str, str], ref: str | None = None) -> str | None:
    """Rebuild the @supabase/ssr session cookie value (single or chunked .0/.1/…).

    If *ref* is provided (derived from SUPABASE_URL), only cookies whose name is
    ``sb-<ref>-auth-token`` (or chunked ``sb-<ref>-auth-token.<N>``) are considered.
    This prevents a foreign project's cookie — sharing the same browser origin — from
    being mistakenly validated against our JWKS/secret.

    If *ref* is None (pure-HS256 installs or custom-domain deployments without a
    recognisable supabase.co host), the original any-match behaviour is preserved.
    """
    if ref:
        prefix = f"sb-{ref}-auth-token"
        relevant = {
            n: v for n, v in cookies.items()
            if n == prefix or n.startswith(f"{prefix}.")
        }
    else:
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
        # @supabase/ssr encodes the value with base64URL (stringToBase64URL),
        # not standard base64 — normalize -/_ to +/ and pad before decoding.
        try:
            payload = raw[len("base64-"):].replace("-", "+").replace("_", "/")
            payload += "=" * (-len(payload) % 4)
            raw = base64.b64decode(payload).decode("utf-8")
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
    # Compute supabase_url, issuer, and project ref at the top so that cookie
    # selection is scoped to OUR project before we attempt any JWT verification.
    supabase_url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    issuer = f"{supabase_url}/auth/v1" if supabase_url else None
    ref = _project_ref(supabase_url) if supabase_url else None

    access_token = _access_token_from_cookie(
        _reassemble_supabase_cookie(dict(request.cookies), ref)
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

    # Fix #4: accept ES256 only (RS256 removed — unused surface).
    if alg == "ES256":
        # Asymmetric path: verify via Supabase JWKS endpoint.
        # Routing on the unverified alg is safe because we only accept the
        # public key matched by `kid` from the JWKS — the shared HS256 secret
        # is never used here, so an attacker cannot substitute keys.
        client = _get_jwks_client()
        if client is None:
            return JSONResponse({"detail": "Supabase JWKS not configured (SUPABASE_URL unset)"}, status_code=503)
        try:
            signing_key = client.get_signing_key_from_jwt(access_token)
            decode_kwargs: dict = dict(
                algorithms=["ES256"],
                audience="authenticated",
            )
            # Fix #1: enforce issuer when SUPABASE_URL is set (it always is in
            # this branch because _get_jwks_client() would have returned None).
            if issuer:
                decode_kwargs["issuer"] = issuer
            claims = jwt.decode(
                access_token,
                signing_key.key,
                **decode_kwargs,
            )
        # Fix #2: network failure → 503, not 401 (must be caught BEFORE PyJWTError).
        except jwt.exceptions.PyJWKClientConnectionError:
            return JSONResponse({"detail": "JWKS endpoint unreachable"}, status_code=503)
        except jwt.PyJWTError:
            return Response(status_code=401)
        except Exception:
            return Response(status_code=401)

    elif alg == "HS256":
        # Symmetric path: verify with the shared secret.
        secret = os.environ.get("SUPABASE_JWT_SECRET", "").strip()
        if not secret:
            return JSONResponse({"detail": "Supabase auth not configured"}, status_code=503)
        try:
            decode_kwargs = dict(
                algorithms=["HS256"],
                audience="authenticated",
            )
            # Fix #1: enforce issuer only when SUPABASE_URL is set; pure-HS256
            # installs (no SUPABASE_URL) skip issuer validation so they don't crash.
            if issuer:
                decode_kwargs["issuer"] = issuer
            claims = jwt.decode(access_token, secret, **decode_kwargs)
        except jwt.PyJWTError:
            return Response(status_code=401)
        # Fix #3: non-PyJWT errors (e.g. malformed secret) → 401, not 500.
        except Exception:
            return Response(status_code=401)

    else:
        # Unknown or disallowed algorithm (including "none", "RS256") → reject.
        return Response(status_code=401)

    email = claims.get("email")
    if not email:
        return Response(status_code=401)
    role = claims.get("user_role") or "agent"
    headers = {"X-Auth-Email": email, "X-Auth-Role": role}
    user_id = claims.get("sub")
    if isinstance(user_id, str) and user_id:
        headers["X-Auth-User-Id"] = user_id
    return Response(status_code=200, headers=headers)
