import base64
import hashlib
import hmac
import json
import time


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def mint_hia_token(email: str, secret: str, ttl: int = 60) -> str:
    now = int(time.time())
    payload = {"exp": now + ttl, "iat": now, "sub": email}
    payload_b64 = _b64url(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    )
    sig = _b64url(
        hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
    )
    return f"{payload_b64}.{sig}"
