"""Tests for backend/auth.py — the require_user dependency."""

import asyncio
import time
import types

import jwt
import pytest
from fastapi import HTTPException

from backend import auth

SECRET = "test-secret"


def req(path, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return types.SimpleNamespace(url=types.SimpleNamespace(path=path), headers=headers)


def make_token(secret=SECRET, **claims):
    payload = {"aud": "authenticated", "exp": int(time.time()) + 3600}
    payload.update(claims)
    return jwt.encode(payload, secret, algorithm="HS256")


def run(coro):
    return asyncio.run(coro)


def test_disabled_without_secret(monkeypatch):
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    # No token, protected path — but auth is off, so it passes.
    assert run(auth.require_user(req("/api/settings"))) is None


def test_exempt_paths(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    for p in ["/api/health", "/webhook/github", "/docs", "/openapi.json", "/index.html"]:
        assert run(auth.require_user(req(p))) is None


def test_missing_token_rejected(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    with pytest.raises(HTTPException) as e:
        run(auth.require_user(req("/api/settings")))
    assert e.value.status_code == 401


def test_valid_token_passes(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    assert run(auth.require_user(req("/api/settings", make_token(email="a@b.com")))) is None


def test_wrong_secret_rejected(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    with pytest.raises(HTTPException) as e:
        run(auth.require_user(req("/api/settings", make_token(secret="wrong"))))
    assert e.value.status_code == 401


def test_expired_token_rejected(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    tok = make_token(exp=int(time.time()) - 10, email="a@b.com")
    with pytest.raises(HTTPException) as e:
        run(auth.require_user(req("/api/settings", tok)))
    assert e.value.status_code == 401


def test_allowlist(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.setenv("ALLOWED_EMAILS", "ok@b.com")
    assert run(auth.require_user(req("/api/settings", make_token(email="ok@b.com")))) is None
    with pytest.raises(HTTPException) as e:
        run(auth.require_user(req("/api/settings", make_token(email="no@b.com"))))
    assert e.value.status_code == 403


def test_asymmetric_token_via_jwks(monkeypatch):
    # Mirrors a current Supabase project: ES256-signed access tokens verified
    # against the JWKS. We generate an EC keypair, sign a token, and stub the
    # JWKS client to return the public key.
    from cryptography.hazmat.primitives.asymmetric import ec
    import types

    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    token = jwt.encode(
        {"aud": "authenticated", "exp": int(time.time()) + 3600, "email": "a@b.com"},
        private_key, algorithm="ES256",
    )

    monkeypatch.setenv("SUPABASE_URL", "https://ref.supabase.co")
    monkeypatch.setattr(auth, "_jwk_client", lambda url: types.SimpleNamespace(
        get_signing_key_from_jwt=lambda t: types.SimpleNamespace(key=public_key)
    ))
    assert run(auth.require_user(req("/api/settings", token))) is None

    # tampered token (wrong key) is rejected
    other = ec.generate_private_key(ec.SECP256R1())
    bad = jwt.encode({"aud": "authenticated", "exp": int(time.time()) + 3600}, other, algorithm="ES256")
    with pytest.raises(HTTPException) as e:
        run(auth.require_user(req("/api/settings", bad)))
    assert e.value.status_code == 401
