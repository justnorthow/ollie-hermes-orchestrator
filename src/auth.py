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
