"""
auth.py — optional Supabase Auth gate for the API.

`require_user` is installed as a global FastAPI dependency. It is a **no-op when
SUPABASE_JWT_SECRET is unset** (local dev / tests), so nothing changes there.
When the secret is set, every `/api/*` request (except health) must carry a valid
Supabase access token: `Authorization: Bearer <jwt>`.

Exempt: `/api/health`, the GitHub webhook (`/webhook/*`, HMAC-verified), the docs
routes, and any non-API path (the SPA is served by the CDN, not this function).

Verification uses the project's HS256 JWT secret. Projects using Supabase's newer
asymmetric (ES256/RS256) signing keys should instead verify against the JWKS at
`https://<ref>.supabase.co/auth/v1/.well-known/jwks.json` (e.g. with
PyJWKClient) — swap `_decode` accordingly.
"""

import os

from fastapi import Request, HTTPException

_EXEMPT_PREFIXES = ("/api/health", "/webhook", "/docs", "/redoc", "/openapi.json")


def _allowed_email(email: str) -> bool:
    allow = os.getenv("ALLOWED_EMAILS", "").strip()
    if not allow:
        return True
    allowed = {e.strip().lower() for e in allow.split(",") if e.strip()}
    return (email or "").lower() in allowed


def _decode(token: str, secret: str) -> dict:
    import jwt

    return jwt.decode(token, secret, algorithms=["HS256"], audience="authenticated")


async def require_user(request: Request):
    secret = os.getenv("SUPABASE_JWT_SECRET")
    if not secret:
        return  # auth disabled (local dev / tests)

    path = request.url.path
    if path.startswith(_EXEMPT_PREFIXES) or not path.startswith("/api/"):
        return

    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed bearer token")

    try:
        claims = _decode(header[len("Bearer "):], secret)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

    if not _allowed_email(claims.get("email")):
        raise HTTPException(status_code=403, detail="Not authorized")
