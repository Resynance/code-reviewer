"""
auth.py — optional Supabase Auth gate for the API.

`require_user` is installed as a global FastAPI dependency. It is a **no-op unless
auth is configured**, so local dev / tests are unaffected. When configured, every
`/api/*` request (except health) must carry a valid Supabase access token:
`Authorization: Bearer <jwt>`.

Supabase signs user tokens either with **asymmetric keys** (ES256/RS256 — the
default for current projects, verified via the project's JWKS) or the legacy
**HS256** secret. Both are supported:

  SUPABASE_URL          e.g. https://<ref>.supabase.co  → JWKS at /auth/v1/.well-known/jwks.json
  SUPABASE_JWKS_URL     explicit JWKS URL (overrides the derived one)
  SUPABASE_JWT_SECRET   legacy HS256 secret (fallback for HS256-signed tokens)

Auth is enabled when any of the above is set. Authorization is **fail closed**:
once auth is on, `ALLOWED_EMAILS` must list the permitted emails (or be `*` to
allow any authenticated user) — an empty value denies everyone, so a public
OAuth provider can't leave the app open. Exempt: `/api/health`, the GitHub
webhook (`/webhook/*`, HMAC-verified), the docs routes, and any non-API path.
"""

import os
import functools

from fastapi import Request, HTTPException

_EXEMPT_PREFIXES = ("/api/health", "/webhook", "/docs", "/redoc", "/openapi.json")


def _auth_enabled() -> bool:
    return bool(
        os.getenv("SUPABASE_URL")
        or os.getenv("SUPABASE_JWKS_URL")
        or os.getenv("SUPABASE_JWT_SECRET")
    )


def _jwks_url():
    if os.getenv("SUPABASE_JWKS_URL"):
        return os.getenv("SUPABASE_JWKS_URL")
    base = os.getenv("SUPABASE_URL")
    if base:
        return base.rstrip("/") + "/auth/v1/.well-known/jwks.json"
    return None


@functools.lru_cache(maxsize=4)
def _jwk_client(url: str):
    import jwt

    return jwt.PyJWKClient(url)


def _check_allowlist(email: str):
    """Authorize a verified user against ALLOWED_EMAILS — **fail closed**.

    With auth enabled, an empty ALLOWED_EMAILS denies everyone (a public OAuth
    provider would otherwise let any account in). Set a comma-separated list, or
    `*` to explicitly allow any authenticated user.
    """
    allow = os.getenv("ALLOWED_EMAILS", "").strip()
    if not allow:
        raise HTTPException(
            status_code=403,
            detail="Access is locked: set ALLOWED_EMAILS (comma-separated, or '*' for any user)",
        )
    if allow == "*":
        return
    allowed = {e.strip().lower() for e in allow.split(",") if e.strip()}
    if (email or "").lower() not in allowed:
        raise HTTPException(status_code=403, detail="Not authorized")


def _decode(token: str) -> dict:
    import jwt

    alg = jwt.get_unverified_header(token).get("alg", "")
    if alg.startswith(("ES", "RS", "PS", "Ed")):  # asymmetric → verify via JWKS
        url = _jwks_url()
        if not url:
            raise RuntimeError("SUPABASE_URL or SUPABASE_JWKS_URL is required for asymmetric tokens")
        signing_key = _jwk_client(url).get_signing_key_from_jwt(token).key
        return jwt.decode(token, signing_key, algorithms=[alg], audience="authenticated")

    secret = os.getenv("SUPABASE_JWT_SECRET")  # legacy HS256
    if not secret:
        raise RuntimeError("SUPABASE_JWT_SECRET is required for HS256 tokens")
    return jwt.decode(token, secret, algorithms=["HS256"], audience="authenticated")


async def require_user(request: Request):
    if not _auth_enabled():
        return  # auth disabled (local dev / tests)

    path = request.url.path
    if path.startswith(_EXEMPT_PREFIXES) or not path.startswith("/api/"):
        return

    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed bearer token")

    try:
        claims = _decode(header[len("Bearer "):])
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

    _check_allowlist(claims.get("email"))
