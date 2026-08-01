import hmac
from fastapi import HTTPException, Request, status


class AuthError(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")


async def require_bearer(request: Request) -> None:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise AuthError()
    token = header[7:].strip()
    expected = request.app.state.config.orchestrator_key
    if not hmac.compare_digest(token, expected):
        raise AuthError()
    # The dashboard nginx injects the bearer on behalf of a browser, so bearer
    # possession alone proves only that the request traversed that proxy. Make
    # browser traffic fail closed unless the front-door auth gate also supplied
    # a validated subject. True service-to-service callers omit this marker and
    # retain the existing bearer-only contract.
    caller = request.headers.get("X-Ollie-Caller", "").strip().lower()
    if caller == "dashboard" and not request.headers.get("X-Auth-User-Id", "").strip():
        raise AuthError()
