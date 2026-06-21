import base64
import hashlib
import hmac
import json
import secrets
import time


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def mint_hia_token(email: str, secret: str, ttl: int = 60) -> str:
    now = int(time.time())
    payload = {
        "exp": now + ttl,
        "iat": now,
        "jti": secrets.token_urlsafe(16),
        "sub": email,
    }
    payload_b64 = _b64url(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    )
    sig = _b64url(
        hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
    )
    return f"{payload_b64}.{sig}"


def mint_sso_token(email: str, role: str, secret: str, ttl: int = 60) -> str:
    """Per-user app SSO token: same hand-rolled format as mint_hia_token, plus a `role` claim."""
    now = int(time.time())
    payload = {
        "exp": now + ttl,
        "iat": now,
        "jti": secrets.token_urlsafe(16),
        "role": role,
        "sub": email,
    }
    payload_b64 = _b64url(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    )
    sig = _b64url(
        hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
    )
    return f"{payload_b64}.{sig}"
