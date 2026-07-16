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
# All tests that set SUPABASE_URL use this ref so cookie names align with scoping.
SUPABASE_URL = "https://abcdef.supabase.co"
OUR_REF = "abcdef"
OUR_COOKIE = f"sb-{OUR_REF}-auth-token"
ISSUER = f"{SUPABASE_URL}/auth/v1"

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_cookie_value(email="a@b.com", role="agent", exp_delta=60, secret=SECRET,
                       issuer=ISSUER):
    """Build a base64-encoded @supabase/ssr cookie with a valid HS256 JWT inside."""
    now = int(time.time())
    payload: dict = {"aud": "authenticated", "sub": "u-1", "email": email,
                     "user_role": role, "iat": now, "exp": now + exp_delta}
    if issuer is not None:
        payload["iss"] = issuer
    access = jwt.encode(payload, secret, algorithm="HS256")
    session = json.dumps({"access_token": access, "token_type": "bearer"})
    return "base64-" + base64.urlsafe_b64encode(session.encode()).rstrip(b"=").decode()


def _wrap_token(token: str) -> str:
    """Wrap a raw JWT string in the @supabase/ssr cookie envelope."""
    session = json.dumps({"access_token": token, "token_type": "bearer"})
    return "base64-" + base64.urlsafe_b64encode(session.encode()).rstrip(b"=").decode()


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
# HS256 fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None  # bypass bearer for unit tests
    return TestClient(app)


# ---------------------------------------------------------------------------
# Existing HS256 tests — updated to include correct `iss` claim
# ---------------------------------------------------------------------------

def test_valid_session_returns_email_and_role(client):
    val = _make_cookie_value(email="broker@x.com", role="compliance")
    r = client.get("/v1/auth/validate", cookies={OUR_COOKIE: val})
    assert r.status_code == 200
    assert r.headers["X-Auth-Email"] == "broker@x.com"
    assert r.headers["X-Auth-Role"] == "compliance"


def test_missing_user_role_defaults_to_agent(client):
    now = int(time.time())
    access = jwt.encode(
        {"aud": "authenticated", "iss": ISSUER, "email": "a@b.com",
         "iat": now, "exp": now + 60},
        SECRET, algorithm="HS256",
    )
    val = "base64-" + base64.urlsafe_b64encode(
        json.dumps({"access_token": access}).encode()).rstrip(b"=").decode()
    r = client.get("/v1/auth/validate", cookies={OUR_COOKIE: val})
    assert r.status_code == 200
    assert r.headers["X-Auth-Role"] == "agent"


def test_validate_returns_user_id_header(client):
    now = int(time.time())
    access = jwt.encode(
        {"aud": "authenticated", "iss": ISSUER, "sub": "11111111-2222-3333-4444-555555555555",
         "email": "a@b.com", "user_role": "agent", "iat": now, "exp": now + 60},
        SECRET, algorithm="HS256",
    )
    val = "base64-" + base64.urlsafe_b64encode(
        json.dumps({"access_token": access}).encode()).rstrip(b"=").decode()
    r = client.get("/v1/auth/validate", cookies={OUR_COOKIE: val})
    assert r.status_code == 200
    assert r.headers["X-Auth-User-Id"] == "11111111-2222-3333-4444-555555555555"


def test_validate_omits_user_id_header_when_no_sub(client):
    now = int(time.time())
    access = jwt.encode(
        {"aud": "authenticated", "iss": ISSUER,
         "email": "a@b.com", "user_role": "agent", "iat": now, "exp": now + 60},
        SECRET, algorithm="HS256",
    )
    val = "base64-" + base64.urlsafe_b64encode(
        json.dumps({"access_token": access}).encode()).rstrip(b"=").decode()
    r = client.get("/v1/auth/validate", cookies={OUR_COOKIE: val})
    assert r.status_code == 200
    assert "X-Auth-User-Id" not in r.headers


def test_chunked_cookie_is_reassembled(client):
    val = _make_cookie_value(email="c@x.com")
    mid = len(val) // 2
    r = client.get("/v1/auth/validate",
                   cookies={f"{OUR_COOKIE}.0": val[:mid], f"{OUR_COOKIE}.1": val[mid:]})
    assert r.status_code == 200
    assert r.headers["X-Auth-Email"] == "c@x.com"


def test_expired_token_is_401(client):
    val = _make_cookie_value(exp_delta=-10)
    r = client.get("/v1/auth/validate", cookies={OUR_COOKIE: val})
    assert r.status_code == 401


def test_wrong_secret_is_401(client):
    val = _make_cookie_value(secret="not-the-secret-and-also-long-enough!!")
    r = client.get("/v1/auth/validate", cookies={OUR_COOKIE: val})
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
    val = _make_cookie_value(issuer=None)  # no SUPABASE_URL → no issuer enforced
    r = TestClient(app).get("/v1/auth/validate", cookies={"sb-x-auth-token": val})
    assert r.status_code == 503


def test_es256_issuer_override_env(monkeypatch, ec_keypair):
    """SUPABASE_ISSUER overrides the SUPABASE_URL-derived issuer expectation —
    the self-hosted split (loopback API URL, public token issuer)."""
    private_pem, public_key = ec_keypair
    now = int(time.time())
    public_issuer = "https://sb-esource.getbilled.io/auth/v1"
    token = jwt.encode(
        {"aud": "authenticated", "iss": public_issuer, "sub": "u-3",
         "email": "lo@example.com", "iat": now, "exp": now + 60},
        private_pem, algorithm="ES256", headers={"kid": "test-kid"},
    )
    cookie_val = _wrap_token(token)
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("SUPABASE_ISSUER", public_issuer)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    mock_client = _make_mock_jwks_client(public_key)
    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None
    with patch("src.api.auth_validate._get_jwks_client", return_value=mock_client):
        # Cookie name matches @supabase/ssr's real derivation: first host label
        # of the browser-facing URL ("sb-esource") -> sb-sb-esource-auth-token.
        # Ref scoping now requires the real name (multi-box cookie coexistence).
        r = TestClient(app).get("/v1/auth/validate",
                                cookies={"sb-sb-esource-auth-token": cookie_val})
    assert r.status_code == 200
    assert r.headers["X-Auth-Email"] == "lo@example.com"


def test_es256_public_issuer_without_override_is_401(monkeypatch, ec_keypair):
    """Without SUPABASE_ISSUER, a public-issuer token against a loopback
    SUPABASE_URL fails the derived-issuer check — enforcement is retained."""
    private_pem, public_key = ec_keypair
    now = int(time.time())
    token = jwt.encode(
        {"aud": "authenticated", "iss": "https://sb-esource.getbilled.io/auth/v1",
         "sub": "u-3", "email": "lo@example.com", "iat": now, "exp": now + 60},
        private_pem, algorithm="ES256", headers={"kid": "test-kid"},
    )
    cookie_val = _wrap_token(token)
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:8000")
    monkeypatch.delenv("SUPABASE_ISSUER", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    mock_client = _make_mock_jwks_client(public_key)
    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None
    with patch("src.api.auth_validate._get_jwks_client", return_value=mock_client):
        r = TestClient(app).get("/v1/auth/validate", cookies={"sb-x-auth-token": cookie_val})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# ES256 / JWKS tests
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
        {"aud": "authenticated", "iss": ISSUER, "sub": "u-2", "email": "es@example.com",
         "user_role": "broker", "iat": now, "exp": now + 60},
        private_pem, algorithm="ES256", headers={"kid": "test-kid"},
    )
    cookie_val = _wrap_token(token)

    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)

    mock_client = _make_mock_jwks_client(public_key)

    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None

    with patch("src.api.auth_validate._get_jwks_client", return_value=mock_client):
        r = TestClient(app).get("/v1/auth/validate", cookies={OUR_COOKIE: cookie_val})

    assert r.status_code == 200
    assert r.headers["X-Auth-Email"] == "es@example.com"
    assert r.headers["X-Auth-Role"] == "broker"


def test_es256_wrong_audience_is_401(monkeypatch, ec_keypair):
    """An ES256 token with the wrong audience claim → 401."""
    private_pem, public_key = ec_keypair
    now = int(time.time())
    token = jwt.encode(
        {"aud": "wrong-audience", "iss": ISSUER, "sub": "u-3", "email": "es@example.com",
         "user_role": "agent", "iat": now, "exp": now + 60},
        private_pem, algorithm="ES256", headers={"kid": "test-kid"},
    )
    cookie_val = _wrap_token(token)

    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)

    mock_client = _make_mock_jwks_client(public_key)

    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None

    with patch("src.api.auth_validate._get_jwks_client", return_value=mock_client):
        r = TestClient(app).get("/v1/auth/validate", cookies={OUR_COOKIE: cookie_val})

    assert r.status_code == 401


def test_es256_expired_token_is_401(monkeypatch, ec_keypair):
    """An expired ES256 token → 401."""
    private_pem, public_key = ec_keypair
    now = int(time.time())
    token = jwt.encode(
        {"aud": "authenticated", "iss": ISSUER, "sub": "u-4", "email": "es@example.com",
         "user_role": "agent", "iat": now - 120, "exp": now - 60},
        private_pem, algorithm="ES256", headers={"kid": "test-kid"},
    )
    cookie_val = _wrap_token(token)

    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)

    mock_client = _make_mock_jwks_client(public_key)

    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None

    with patch("src.api.auth_validate._get_jwks_client", return_value=mock_client):
        r = TestClient(app).get("/v1/auth/validate", cookies={OUR_COOKIE: cookie_val})

    assert r.status_code == 401


def test_alg_none_is_401(monkeypatch):
    """A token with alg:none must be rejected (never verify unsigned tokens)."""
    now = int(time.time())
    # Manually craft an alg:none token (PyJWT won't encode it so we build the raw form)
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    payload_data = {"aud": "authenticated", "iss": ISSUER, "sub": "u-5",
                    "email": "none@example.com", "user_role": "admin",
                    "iat": now, "exp": now + 60}
    payload = base64.urlsafe_b64encode(json.dumps(payload_data).encode()).rstrip(b"=").decode()
    token = f"{header}.{payload}."  # unsigned

    cookie_val = _wrap_token(token)

    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)

    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None

    r = TestClient(app).get("/v1/auth/validate", cookies={OUR_COOKIE: cookie_val})
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


# ---------------------------------------------------------------------------
# NEW security tests (from review)
# ---------------------------------------------------------------------------

def test_wrong_issuer_is_401(monkeypatch):
    """A valid HS256 token whose iss is a different Supabase project → 401."""
    now = int(time.time())
    token = jwt.encode(
        {"aud": "authenticated", "iss": "https://other-project.supabase.co/auth/v1",
         "sub": "u-8", "email": "attacker@evil.com", "user_role": "admin",
         "iat": now, "exp": now + 60},
        SECRET, algorithm="HS256",
    )
    cookie_val = _wrap_token(token)

    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)

    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None

    r = TestClient(app).get("/v1/auth/validate", cookies={OUR_COOKIE: cookie_val})
    assert r.status_code == 401


def test_es256_unknown_kid_is_401(monkeypatch, ec_keypair):
    """An ES256 token whose kid is not in the JWKS → 401 (PyJWKClientError)."""
    private_pem, _ = ec_keypair
    now = int(time.time())
    token = jwt.encode(
        {"aud": "authenticated", "iss": ISSUER, "sub": "u-9", "email": "es@example.com",
         "user_role": "agent", "iat": now, "exp": now + 60},
        private_pem, algorithm="ES256", headers={"kid": "unknown-kid"},
    )
    cookie_val = _wrap_token(token)

    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)

    mock_client = MagicMock()
    mock_client.get_signing_key_from_jwt.side_effect = jwt.exceptions.PyJWKClientError("kid not found")

    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None

    with patch("src.api.auth_validate._get_jwks_client", return_value=mock_client):
        r = TestClient(app).get("/v1/auth/validate", cookies={OUR_COOKIE: cookie_val})

    assert r.status_code == 401


def test_es256_jwks_network_failure_is_503(monkeypatch, ec_keypair):
    """JWKS endpoint unreachable → 503, not 401 (network failures must not silently lock out users)."""
    private_pem, _ = ec_keypair
    now = int(time.time())
    token = jwt.encode(
        {"aud": "authenticated", "iss": ISSUER, "sub": "u-10", "email": "es@example.com",
         "user_role": "agent", "iat": now, "exp": now + 60},
        private_pem, algorithm="ES256", headers={"kid": "test-kid"},
    )
    cookie_val = _wrap_token(token)

    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)

    mock_client = MagicMock()
    mock_client.get_signing_key_from_jwt.side_effect = jwt.exceptions.PyJWKClientConnectionError("unreachable")

    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None

    with patch("src.api.auth_validate._get_jwks_client", return_value=mock_client):
        r = TestClient(app).get("/v1/auth/validate", cookies={OUR_COOKIE: cookie_val})

    assert r.status_code == 503


def test_alg_confusion_forged_hs256_with_ec_public_key_is_401(monkeypatch, ec_keypair):
    """Algorithm-confusion attack: HS256 token whose HMAC was computed with the EC
    public key bytes (raw DER) → 401.

    Modern PyJWT rejects PEM-encoded asymmetric material as an HMAC key (InvalidKeyError),
    so the attacker would use the raw DER bytes instead. The validator must reject this
    because the server's real SUPABASE_JWT_SECRET is a different value — the forged HMAC
    won't verify against it, and the Exception fallback ensures no 500 leaks.
    """
    import hmac as _hmac
    import hashlib as _hashlib

    private_key_pem, public_key_obj = ec_keypair
    # Serialize the EC public key to raw DER bytes — what an attacker would use as
    # the HMAC "secret" in a classic alg-confusion attack.
    public_der_bytes = public_key_obj.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    now = int(time.time())
    # Manually build the forged HS256 token using the DER bytes as the HMAC key,
    # bypassing PyJWT's PEM-format guard.
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "HS256", "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()
    payload_data = {"aud": "authenticated", "iss": ISSUER, "sub": "u-11",
                    "email": "attacker@evil.com", "user_role": "admin",
                    "iat": now, "exp": now + 60}
    payload = base64.urlsafe_b64encode(
        json.dumps(payload_data).encode()
    ).rstrip(b"=").decode()
    signing_input = f"{header}.{payload}".encode()
    forged_sig = _hmac.new(public_der_bytes, signing_input, _hashlib.sha256).digest()
    forged_sig_b64 = base64.urlsafe_b64encode(forged_sig).rstrip(b"=").decode()
    forged_token = f"{header}.{payload}.{forged_sig_b64}"

    cookie_val = _wrap_token(forged_token)

    # The real secret is something entirely different — the forged HMAC won't verify.
    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)

    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None

    r = TestClient(app).get("/v1/auth/validate", cookies={OUR_COOKIE: cookie_val})
    assert r.status_code == 401


def test_rs256_is_rejected(monkeypatch):
    """RS256 tokens are no longer accepted (unused surface) → 401."""
    # We don't need a real RSA key — just a token whose header claims RS256.
    # jwt.get_unverified_header will read it and we expect early rejection.
    now = int(time.time())
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "RS256", "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()
    payload_data = {"aud": "authenticated", "iss": ISSUER, "sub": "u-12",
                    "email": "rs@example.com", "user_role": "agent",
                    "iat": now, "exp": now + 60}
    payload = base64.urlsafe_b64encode(json.dumps(payload_data).encode()).rstrip(b"=").decode()
    # Fake signature — will be rejected before signature verification anyway.
    token = f"{header}.{payload}.fakesig"
    cookie_val = _wrap_token(token)

    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)

    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None

    r = TestClient(app).get("/v1/auth/validate", cookies={OUR_COOKIE: cookie_val})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Regression tests: multi-project cookie scoping (the production bug)
# ---------------------------------------------------------------------------

def test_foreign_project_cookie_ignored_when_our_cookie_present(monkeypatch):
    """Two cookies on the same origin: one from a foreign Supabase project (signed with
    a different secret) and one from our project (valid).  Validate must pick OUR cookie
    and return 200 with the correct email — not the foreign one which would cause a 401."""
    now = int(time.time())
    # Foreign project cookie: signed with a different secret — would fail our HMAC check.
    foreign_token = jwt.encode(
        {"aud": "authenticated", "iss": "https://zzznewsletter.supabase.co/auth/v1",
         "sub": "f-1", "email": "foreign@other.com", "user_role": "agent",
         "iat": now, "exp": now + 60},
        "foreign-project-secret-32bytes-xx", algorithm="HS256",
    )
    foreign_cookie_val = _wrap_token(foreign_token)

    # Our project cookie: valid, signed with SECRET, correct issuer.
    our_cookie_val = _make_cookie_value(email="real@ours.com", role="broker")

    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)

    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None

    r = TestClient(app).get(
        "/v1/auth/validate",
        cookies={
            "sb-zzznewsletter-auth-token": foreign_cookie_val,
            OUR_COOKIE: our_cookie_val,
        },
    )
    assert r.status_code == 200
    assert r.headers["X-Auth-Email"] == "real@ours.com"
    assert r.headers["X-Auth-Role"] == "broker"


def test_only_foreign_project_cookie_present_is_401(monkeypatch):
    """Only a foreign project's cookie is present (our cookie is absent) → 401.
    With scoping, the foreign cookie is invisible to the validator — there is no
    cookie to extract, so the response is 401 (no session)."""
    now = int(time.time())
    foreign_token = jwt.encode(
        {"aud": "authenticated", "iss": "https://zzznewsletter.supabase.co/auth/v1",
         "sub": "f-2", "email": "foreign@other.com", "user_role": "agent",
         "iat": now, "exp": now + 60},
        "foreign-project-secret-32bytes-xx", algorithm="HS256",
    )
    foreign_cookie_val = _wrap_token(foreign_token)

    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)

    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None

    r = TestClient(app).get(
        "/v1/auth/validate",
        cookies={"sb-zzznewsletter-auth-token": foreign_cookie_val},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Multi-box cookie coexistence (Domain=.jnow.io ships every box's cookie to
# every sibling host). Regression tests for the chunk-interleave bug: two
# chunked sb-*-auth-token cookie sets on one request must never be
# concatenated into one garbage value.
# ---------------------------------------------------------------------------

def _split_chunks(value: str, n: int = 2) -> list[str]:
    """Split a cookie value into n roughly-equal chunks (@supabase/ssr style)."""
    size = -(-len(value) // n)
    return [value[i * size:(i + 1) * size] for i in range(n)]


def test_custom_domain_ref_scopes_to_our_cookie(monkeypatch):
    """Self-hosted custom domain: ref derives from the public host's first label,
    so a sibling box's chunked cookie on the same request is ignored."""
    public_url = "https://sb-ollie.jnow.io"
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.setenv("SUPABASE_URL", public_url)
    monkeypatch.delenv("SUPABASE_ISSUER", raising=False)
    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None
    c = TestClient(app)

    ours = _make_cookie_value(email="jb@jnow.io", issuer=f"{public_url}/auth/v1")
    ours_chunks = _split_chunks(ours)
    foreign = _make_cookie_value(email="jb@jnow.io", secret="other-secret-32-bytes-long!!!!!!",
                                 issuer="https://sb-olliesandbox.jnow.io/auth/v1")
    foreign_chunks = _split_chunks(foreign)

    cookies = {
        # Foreign box's cookie sorts FIRST alphabetically at equal chunk index —
        # the interleave bug concatenates across both sets and breaks parsing.
        "sb-sb-a-sandbox-auth-token.0": foreign_chunks[0],
        "sb-sb-a-sandbox-auth-token.1": foreign_chunks[1],
        "sb-sb-ollie-auth-token.0": ours_chunks[0],
        "sb-sb-ollie-auth-token.1": ours_chunks[1],
    }
    r = c.get("/v1/auth/validate", cookies=cookies)
    assert r.status_code == 200
    assert r.headers["X-Auth-Email"] == "jb@jnow.io"


def test_loopback_url_derives_ref_from_issuer(monkeypatch):
    """Self-hosted split: SUPABASE_URL is loopback (Kong), SUPABASE_ISSUER carries
    the browser-facing origin — the cookie ref must derive from the ISSUER host."""
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("SUPABASE_ISSUER", "https://sb-ollie.jnow.io/auth/v1")
    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None
    c = TestClient(app)

    ours = _make_cookie_value(email="jb@jnow.io", issuer="https://sb-ollie.jnow.io/auth/v1")
    ours_chunks = _split_chunks(ours)
    junk = _make_cookie_value(secret="other-secret-32-bytes-long!!!!!!",
                              issuer="https://sb-other.jnow.io/auth/v1")
    junk_chunks = _split_chunks(junk)
    cookies = {
        "sb-sb-a-other-auth-token.0": junk_chunks[0],
        "sb-sb-a-other-auth-token.1": junk_chunks[1],
        "sb-sb-ollie-auth-token.0": ours_chunks[0],
        "sb-sb-ollie-auth-token.1": ours_chunks[1],
    }
    r = c.get("/v1/auth/validate", cookies=cookies)
    assert r.status_code == 200
    assert r.headers["X-Auth-Email"] == "jb@jnow.io"


def test_no_ref_fallback_never_interleaves_chunk_sets(monkeypatch):
    """No derivable ref at all: the fallback must group chunks per base cookie
    name and try each coherent candidate — never mix chunk sets."""
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ISSUER", raising=False)
    app = FastAPI()
    app.include_router(auth_validate_router)
    app.dependency_overrides[require_bearer] = lambda: None
    c = TestClient(app)

    ours = _make_cookie_value(email="jb@jnow.io", issuer=None)
    ours_chunks = _split_chunks(ours)
    junk = _make_cookie_value(secret="other-secret-32-bytes-long!!!!!!", issuer=None)
    junk_chunks = _split_chunks(junk)
    cookies = {
        "sb-aaaa-auth-token.0": junk_chunks[0],
        "sb-aaaa-auth-token.1": junk_chunks[1],
        "sb-zzzz-auth-token.0": ours_chunks[0],
        "sb-zzzz-auth-token.1": ours_chunks[1],
    }
    r = c.get("/v1/auth/validate", cookies=cookies)
    assert r.status_code == 200
    assert r.headers["X-Auth-Email"] == "jb@jnow.io"
