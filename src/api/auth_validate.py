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


_HOST_LABEL_RE = re.compile(r"^https?://([a-z0-9-]+)\.[a-z0-9.:-]+", re.IGNORECASE)


def _cookie_ref(supabase_url: str, issuer: str | None = None) -> str | None:
    """Derive the @supabase/ssr cookie ref (cookie name = ``sb-<ref>-auth-token``).

    supabase-js names the session cookie after the FIRST HOST LABEL of the
    browser-facing URL: the project ref on hosted ``*.supabase.co``, the
    subdomain label on custom domains (https://sb-ollie.jnow.io → "sb-ollie").
    On split self-hosted boxes SUPABASE_URL may be loopback (Kong) while the
    browser-facing origin lives in SUPABASE_ISSUER — so prefer the issuer host
    when it parses. IP/loopback hosts yield no meaningful label → None, which
    routes callers to the per-candidate fallback.
    """
    for candidate in (issuer, supabase_url):
        if not candidate:
            continue
        m = _HOST_LABEL_RE.match(candidate.strip())
        if m and not m.group(1).isdigit():
            return m.group(1)
    return None


def _reassemble_supabase_cookie(cookies: dict[str, str], ref: str | None = None) -> str | None:
    """Rebuild the @supabase/ssr session cookie value (single or chunked .0/.1/…).

    If *ref* is provided (derived from SUPABASE_URL), only cookies whose name is
    ``sb-<ref>-auth-token`` (or chunked ``sb-<ref>-auth-token.<N>``) are considered.
    This prevents a foreign project's cookie — sharing the same browser origin — from
    being mistakenly validated against our JWKS/secret.

    If *ref* is None (pure-HS256 installs or custom-domain deployments without a
    recognisable supabase.co host), the original any-match behaviour is preserved.
    """
    candidates = _candidate_cookie_values(cookies, ref)
    return candidates[0] if candidates else None


def _candidate_cookie_values(cookies: dict[str, str], ref: str | None = None) -> list[str]:
    """Session-cookie candidates — one per BASE cookie name, chunks joined in order.

    With ``Domain=.jnow.io`` every sibling box's session cookie arrives on the
    same request (e.g. ``sb-sb-ollie-…`` AND ``sb-sb-olliesandbox-…``). Chunk
    sets from different base names must NEVER be mixed: sorting all chunk names
    by numeric suffix alone interleaves two cookies' halves into one garbage
    value (the multi-box 401 bug). When *ref* is known only its cookie is
    considered; otherwise each coherent per-base candidate is returned and the
    caller tries them in turn.
    """
    if ref:
        prefix = f"sb-{ref}-auth-token"
        relevant = {
            n: v for n, v in cookies.items()
            if n == prefix or n.startswith(f"{prefix}.")
        }
    else:
        relevant = {n: v for n, v in cookies.items() if _AUTH_TOKEN_RE.search(n)}
    by_base: dict[str, dict[int | None, str]] = {}
    for n, v in relevant.items():
        m = _CHUNK_SUFFIX_RE.search(n)
        base = n[: m.start()] if m else n
        by_base.setdefault(base, {})[int(m.group(1)) if m else None] = v
    candidates: list[str] = []
    for base in sorted(by_base):
        parts = by_base[base]
        chunk_keys = sorted(k for k in parts if k is not None)
        if chunk_keys:
            candidates.append("".join(parts[k] for k in chunk_keys))
        elif None in parts:
            candidates.append(parts[None])
    return candidates


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
    # Self-hosted split: on a box with a co-located Supabase stack the
    # orchestrator reaches Kong via loopback (SUPABASE_URL=http://127.0.0.1:8000)
    # while GoTrue mints tokens with the browser-facing public issuer
    # (GOTRUE_JWT_ISSUER, e.g. https://sb-<host>/auth/v1). SUPABASE_ISSUER
    # carries that expected issuer; without it the check falls back to deriving
    # from SUPABASE_URL — correct for hosted projects, where API URL and token
    # issuer share an origin.
    issuer_env = os.environ.get("SUPABASE_ISSUER", "").strip().rstrip("/")
    issuer = issuer_env or (f"{supabase_url}/auth/v1" if supabase_url else None)
    # Cookie ref derives from the BROWSER-FACING origin: issuer first (split
    # self-hosted boxes point SUPABASE_URL at loopback Kong), then SUPABASE_URL
    # (hosted projects and public custom domains).
    ref = _cookie_ref(supabase_url, issuer_env or None)

    # One candidate per base cookie name — sibling boxes' cookies coexist on
    # this origin (Domain=.jnow.io), so try each coherent candidate in turn.
    tokens = [
        t for t in (
            _access_token_from_cookie(c)
            for c in _candidate_cookie_values(dict(request.cookies), ref)
        ) if t
    ]
    if not tokens:
        return Response(status_code=401)

    claims: dict | None = None
    last_error: Response = Response(status_code=401)
    for access_token in tokens:
        result = _verify_token(access_token, issuer)
        if isinstance(result, dict):
            claims = result
            break
        # A 5xx (JWKS unreachable / not configured) is more informative than a
        # generic 401 from a sibling box's token — keep the strongest error.
        if result.status_code != 401 or last_error.status_code == 401:
            last_error = result
    if claims is None:
        return last_error

    email = claims.get("email")
    if not email:
        return Response(status_code=401)
    role = claims.get("user_role") or "agent"
    headers = {"X-Auth-Email": email, "X-Auth-Role": role}
    user_id = claims.get("sub")
    if isinstance(user_id, str) and user_id:
        headers["X-Auth-User-Id"] = user_id
    return Response(status_code=200, headers=headers)


def _verify_token(access_token: str, issuer: str | None) -> dict | Response:
    """Verify one access token; return its claims dict or the error Response."""
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

    return claims
