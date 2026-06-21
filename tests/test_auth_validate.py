import base64
import json
import time
from unittest.mock import MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth_validate import router as auth_validate_router
from src.auth import require_bearer

SECRET = "test-supabase-secret-32bytes-long!!"  # >=32 bytes to avoid InsecureKeyLengthWarning

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_cookie_value(email="a@b.com", role="agent", exp_delta=60, secret=SECRET):
    now = int(time.time())
    access = jwt.encode(
        {"aud": "authenticated", "sub": "u-1", "email": email,
         "user_role": role, "iat": now, "exp": now + exp_delta},
        secret, algorithm="HS256",
    )
    session = json.dumps({"access_token": access, "token_type": "bearer"})
    return "base64-" + base64.b64encode(session.encode()).decode()


def _wrap_token(token: str) -> str:
    """Wrap a raw JWT string in the @supabase/ssr cookie envelope."""
    session = json.dumps({"access_token": token, "token_type": "bearer"})
    return "base64-" + base64.b64encode(session.encode()).decode()


# ---------------------------------------------------------------------------
# EC key fixture for ES256 tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ec_keypair():
    """Return (private_key_pem_bytes, ec_public_key_object) for ES256 tests."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key = private_key.public_key()
    return private_pem, public_key


# ---------------------------------------------------------------------------
# HS256 fixtures (existing tests unchanged, but using longer secret)
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None  # bypass bearer for unit tests
    return TestClient(app)


# ---------------------------------------------------------------------------
# Existing HS256 tests (unchanged behaviour; secret updated to >=32 bytes)
# ---------------------------------------------------------------------------

def test_valid_session_returns_email_and_role(client):
    val = _make_cookie_value(email="broker@x.com", role="compliance")
    r = client.get("/v1/auth/validate", cookies={"sb-abcd-auth-token": val})
    assert r.status_code == 200
    assert r.headers["X-Auth-Email"] == "broker@x.com"
    assert r.headers["X-Auth-Role"] == "compliance"


def test_missing_user_role_defaults_to_agent(client):
    now = int(time.time())
    access = jwt.encode({"aud": "authenticated", "email": "a@b.com",
                         "iat": now, "exp": now + 60}, SECRET, algorithm="HS256")
    val = "base64-" + base64.b64encode(
        json.dumps({"access_token": access}).encode()).decode()
    r = client.get("/v1/auth/validate", cookies={"sb-x-auth-token": val})
    assert r.status_code == 200
    assert r.headers["X-Auth-Role"] == "agent"


def test_chunked_cookie_is_reassembled(client):
    val = _make_cookie_value(email="c@x.com")
    mid = len(val) // 2
    r = client.get("/v1/auth/validate",
                   cookies={"sb-x-auth-token.0": val[:mid], "sb-x-auth-token.1": val[mid:]})
    assert r.status_code == 200
    assert r.headers["X-Auth-Email"] == "c@x.com"


def test_expired_token_is_401(client):
    val = _make_cookie_value(exp_delta=-10)
    r = client.get("/v1/auth/validate", cookies={"sb-x-auth-token": val})
    assert r.status_code == 401


def test_wrong_secret_is_401(client):
    val = _make_cookie_value(secret="not-the-secret-and-also-long-enough!!")
    r = client.get("/v1/auth/validate", cookies={"sb-x-auth-token": val})
    assert r.status_code == 401


def test_no_cookie_is_401(client):
    r = client.get("/v1/auth/validate")
    assert r.status_code == 401


def test_unconfigured_secret_is_503(monkeypatch):
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None
    # HS256 token but no secret configured and no SUPABASE_URL → 503
    # Must use a real HS256-shaped cookie so the alg-routing reaches the secret check.
    val = _make_cookie_value()
    r = TestClient(app).get("/v1/auth/validate", cookies={"sb-x-auth-token": val})
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# ES256 / JWKS tests (new)
# ---------------------------------------------------------------------------

def _make_mock_jwks_client(public_key):
    """Return a mock PyJWKClient whose get_signing_key_from_jwt returns public_key."""
    mock_signing_key = MagicMock()
    mock_signing_key.key = public_key
    mock_client = MagicMock()
    mock_client.get_signing_key_from_jwt.return_value = mock_signing_key
    return mock_client


def test_es256_valid_token_returns_200(monkeypatch, ec_keypair):
    """A valid ES256 JWT verified via mocked JWKS returns 200 with correct headers."""
    private_pem, public_key = ec_keypair
    now = int(time.time())
    token = jwt.encode(
        {"aud": "authenticated", "sub": "u-2", "email": "es@example.com",
         "user_role": "broker", "iat": now, "exp": now + 60},
        private_pem, algorithm="ES256", headers={"kid": "test-kid"},
    )
    cookie_val = _wrap_token(token)

    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)

    mock_client = _make_mock_jwks_client(public_key)

    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None

    with patch("src.api.auth_validate._get_jwks_client", return_value=mock_client):
        r = TestClient(app).get("/v1/auth/validate", cookies={"sb-x-auth-token": cookie_val})

    assert r.status_code == 200
    assert r.headers["X-Auth-Email"] == "es@example.com"
    assert r.headers["X-Auth-Role"] == "broker"


def test_es256_wrong_audience_is_401(monkeypatch, ec_keypair):
    """An ES256 token with the wrong audience claim → 401."""
    private_pem, public_key = ec_keypair
    now = int(time.time())
    token = jwt.encode(
        {"aud": "wrong-audience", "sub": "u-3", "email": "es@example.com",
         "user_role": "agent", "iat": now, "exp": now + 60},
        private_pem, algorithm="ES256", headers={"kid": "test-kid"},
    )
    cookie_val = _wrap_token(token)

    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)

    mock_client = _make_mock_jwks_client(public_key)

    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None

    with patch("src.api.auth_validate._get_jwks_client", return_value=mock_client):
        r = TestClient(app).get("/v1/auth/validate", cookies={"sb-x-auth-token": cookie_val})

    assert r.status_code == 401


def test_es256_expired_token_is_401(monkeypatch, ec_keypair):
    """An expired ES256 token → 401."""
    private_pem, public_key = ec_keypair
    now = int(time.time())
    token = jwt.encode(
        {"aud": "authenticated", "sub": "u-4", "email": "es@example.com",
         "user_role": "agent", "iat": now - 120, "exp": now - 60},
        private_pem, algorithm="ES256", headers={"kid": "test-kid"},
    )
    cookie_val = _wrap_token(token)

    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)

    mock_client = _make_mock_jwks_client(public_key)

    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None

    with patch("src.api.auth_validate._get_jwks_client", return_value=mock_client):
        r = TestClient(app).get("/v1/auth/validate", cookies={"sb-x-auth-token": cookie_val})

    assert r.status_code == 401


def test_alg_none_is_401(monkeypatch):
    """A token with alg:none must be rejected (never verify unsigned tokens)."""
    now = int(time.time())
    # Manually craft an alg:none token (PyJWT won't encode it so we build the raw form)
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    payload_data = {"aud": "authenticated", "sub": "u-5", "email": "none@example.com",
                    "user_role": "admin", "iat": now, "exp": now + 60}
    payload = base64.urlsafe_b64encode(json.dumps(payload_data).encode()).rstrip(b"=").decode()
    token = f"{header}.{payload}."  # unsigned

    cookie_val = _wrap_token(token)

    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)

    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None

    r = TestClient(app).get("/v1/auth/validate", cookies={"sb-x-auth-token": cookie_val})
    assert r.status_code == 401


def test_es256_no_supabase_url_is_503(monkeypatch, ec_keypair):
    """ES256 token but SUPABASE_URL is not configured → 503 (can't fetch JWKS)."""
    private_pem, _ = ec_keypair
    now = int(time.time())
    token = jwt.encode(
        {"aud": "authenticated", "sub": "u-6", "email": "es@example.com",
         "user_role": "agent", "iat": now, "exp": now + 60},
        private_pem, algorithm="ES256", headers={"kid": "test-kid"},
    )
    cookie_val = _wrap_token(token)

    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)

    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None

    # _get_jwks_client returns None when SUPABASE_URL is unset
    with patch("src.api.auth_validate._get_jwks_client", return_value=None):
        r = TestClient(app).get("/v1/auth/validate", cookies={"sb-x-auth-token": cookie_val})

    assert r.status_code == 503


def test_hs256_with_no_secret_is_503(monkeypatch):
    """HS256 token but SUPABASE_JWT_SECRET is not configured → 503."""
    now = int(time.time())
    # We can sign with a local secret just to get a well-formed HS256 token
    token = jwt.encode(
        {"aud": "authenticated", "sub": "u-7", "email": "hs@example.com",
         "user_role": "agent", "iat": now, "exp": now + 60},
        "some-local-secret-thats-32bytes!!", algorithm="HS256",
    )
    cookie_val = _wrap_token(token)

    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)

    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None

    r = TestClient(app).get("/v1/auth/validate", cookies={"sb-x-auth-token": cookie_val})
    assert r.status_code == 503
