"""Google sign-in is required before a trading call when the desk lock is on."""
from __future__ import annotations

import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import desk_lock
from app.core.desk_lock import install_desk_lock, verify_google_token


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _enc_int(value: int) -> str:
    length = max(1, (value.bit_length() + 7) // 8)
    return _b64(value.to_bytes(length, "big"))


@pytest.fixture
def google_key():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private.public_key().public_numbers()
    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "alg": "RS256",
                "use": "sig",
                "kid": "test-key",
                "n": _enc_int(numbers.n),
                "e": _enc_int(numbers.e),
            }
        ]
    }
    return private, jwks


def _token(private, payload: dict, *, kid: str = "test-key", alg: str = "RS256") -> str:
    header = _b64(json.dumps({"alg": alg, "typ": "JWT", "kid": kid}).encode())
    body = _b64(json.dumps(payload).encode())
    signing = f"{header}.{body}".encode()
    signature = private.sign(signing, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{body}.{_b64(signature)}"


def _claims(**overrides):
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": "paltra-client",
        "exp": now + 300,
        "email": "owner@gmail.com",
        "email_verified": True,
    }
    claims.update(overrides)
    return claims


def test_valid_google_token_is_accepted(google_key):
    private, jwks = google_key
    email, reason = verify_google_token(
        _token(private, _claims()),
        jwks,
        client_id="paltra-client",
        allowed={"owner@gmail.com"},
    )
    assert reason == ""
    assert email == "owner@gmail.com"


def test_other_google_account_is_rejected(google_key):
    private, jwks = google_key
    email, reason = verify_google_token(
        _token(private, _claims(email="someoneelse@gmail.com")),
        jwks,
        client_id="paltra-client",
        allowed={"owner@gmail.com"},
    )
    assert email is None
    assert "not allowed" in reason


def test_unverified_email_is_rejected(google_key):
    private, jwks = google_key
    email, _reason = verify_google_token(
        _token(private, _claims(email_verified=False)),
        jwks,
        client_id="paltra-client",
        allowed={"owner@gmail.com"},
    )
    assert email is None


def test_wrong_audience_and_bad_signature_are_rejected(google_key):
    private, jwks = google_key
    email, _reason = verify_google_token(
        _token(private, _claims(aud="other-client")),
        jwks,
        client_id="paltra-client",
        allowed={"owner@gmail.com"},
    )
    assert email is None
    forged = _token(private, _claims())[:-4] + "abcd"
    email, _reason = verify_google_token(
        forged,
        jwks,
        client_id="paltra-client",
        allowed={"owner@gmail.com"},
    )
    assert email is None


@pytest.fixture
def client(monkeypatch, google_key):
    private, jwks = google_key
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "paltra-client")
    monkeypatch.setenv("GOOGLE_ALLOWED_EMAILS", "owner@gmail.com")
    monkeypatch.setenv("ENCRYPTION_KEY", "test-signing-key")
    desk_lock.clear_failures()

    async def fake_jwks():
        return jwks

    monkeypatch.setattr(desk_lock, "fetch_jwks", fake_jwks)
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/manual/account")
    def account():
        return {"execution": "paper"}

    @app.get("/trade/")
    def trade():
        return {"page": "trade"}

    install_desk_lock(app)
    return TestClient(app), private


def _session_header(response) -> dict[str, str]:
    token = response.cookies.get("desk_session")
    assert token
    issued = response.headers["set-cookie"].lower()
    assert "httponly" in issued
    assert "secure" in issued
    return {"cookie": f"desk_session={token}"}


def test_sma_terminal_requires_google_sign_in(client):
    http, _private = client
    assert http.get("/sma/api/state").status_code == 401


def test_trading_api_requires_google_sign_in(client):
    http, _private = client
    assert http.get("/api/manual/account").status_code == 401
    assert http.get("/api/health").status_code == 200
    page = http.get("/trade/", follow_redirects=False)
    assert page.status_code == 302
    assert page.headers["location"] == "/login/"


def test_google_sign_in_opens_the_desk(client):
    http, private = client
    signed = http.post("/api/session/google", json={"credential": _token(private, _claims())})
    assert signed.status_code == 200
    assert signed.json()["email"] == "owner@gmail.com"
    opened = http.get("/api/manual/account", headers=_session_header(signed))
    assert opened.status_code == 200


def test_lock_stays_off_until_google_is_configured(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_ALLOWED_EMAILS", raising=False)
    app = FastAPI()

    @app.get("/api/manual/account")
    def account():
        return {"ok": True}

    install_desk_lock(app)
    http = TestClient(app)
    assert http.get("/api/manual/account").status_code == 200
