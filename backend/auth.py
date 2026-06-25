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
a user must be on the runtime allowlist table (managed in the app via
`core/access_store.py`, no redeploy) or in the `ALLOWED_EMAILS` env bootstrap
(comma-separated, or `*` for any authenticated user). If both are empty, everyone
is denied, so a public OAuth provider can't leave the app open. Exempt:
`/api/health`, the GitHub webhook (`/webhook/*`, HMAC-verified), the docs routes,
and any non-API path.
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


def _expected_issuer():
    base = os.getenv("SUPABASE_URL", "").strip()
    if not base:
        return None
    return base.rstrip("/") + "/auth/v1"


@functools.lru_cache(maxsize=4)
def _jwk_client(url: str):
    import jwt

    return jwt.PyJWKClient(url)


_GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}


def _canonical_email(email: str) -> str:
    """Lowercase + trim, and canonicalize Gmail addresses.

    Gmail ignores dots in the local part and anything after a ``+``, so
    ``Maxwell.Turner+ci@gmail.com`` and ``maxwellturner@gmail.com`` are the same
    mailbox. Without this, a user added under one spelling is denied when they
    sign in (e.g. via OAuth) under another. Non-Gmail addresses are only
    lowercased/trimmed — dots and ``+`` can be significant elsewhere.
    """
    e = (email or "").strip().lower()
    if "@" not in e:
        return e
    local, _, domain = e.partition("@")
    if domain in _GMAIL_DOMAINS:
        local = local.split("+", 1)[0].replace(".", "")
    return f"{local}@{domain}"


def _check_allowlist(email: str):
    """Authorize a verified user — **fail closed**.

    A user is allowed if their email is in the runtime allowlist table
    (managed in the app, no redeploy) OR in the ALLOWED_EMAILS env bootstrap
    (comma-separated, or `*` for any authenticated user). If neither lists them —
    and the lists are empty — access is denied. Matching is Gmail-aware (dots and
    ``+tags`` are ignored for Gmail), so the same person isn't locked out by a
    cosmetically different spelling of their address.
    """
    canon = _canonical_email(email)

    env_allow = os.getenv("ALLOWED_EMAILS", "").strip()
    if env_allow == "*":
        return
    env_set = {e.strip().lower() for e in env_allow.split(",") if e.strip()}
    if canon and canon in {_canonical_email(e) for e in env_set}:
        return

    try:
        import access_store
        db_set = access_store.allowed_emails_cached()
    except Exception:
        db_set = set()
    if canon and canon in {_canonical_email(e) for e in db_set}:
        return

    if not env_set and not db_set:
        raise HTTPException(status_code=403, detail="Access is locked: no users are on the allowlist")
    raise HTTPException(status_code=403, detail="Not authorized")


def _decode(token: str) -> dict:
    import jwt

    alg = jwt.get_unverified_header(token).get("alg", "")
    if alg in {"", "none", "None"}:
        raise RuntimeError("JWT is missing a supported signing algorithm")
    kwargs = {"audience": "authenticated"}
    issuer = _expected_issuer()
    if issuer:
        kwargs["issuer"] = issuer
    if alg.startswith(("ES", "RS", "PS", "Ed")):  # asymmetric → verify via JWKS
        url = _jwks_url()
        if not url:
            raise RuntimeError("SUPABASE_URL or SUPABASE_JWKS_URL is required for asymmetric tokens")
        signing_key = _jwk_client(url).get_signing_key_from_jwt(token).key
        return jwt.decode(token, signing_key, algorithms=[alg], **kwargs)

    secret = os.getenv("SUPABASE_JWT_SECRET")  # legacy HS256
    if not secret:
        raise RuntimeError("SUPABASE_JWT_SECRET is required for HS256 tokens")
    return jwt.decode(token, secret, algorithms=["HS256"], **kwargs)


def _get_admin_email() -> "str | None":
    """Return the configured admin email, or None if not determinable.

    Priority: ADMIN_EMAIL env var → first entry in ALLOWED_EMAILS (when not '*').
    """
    explicit = os.getenv("ADMIN_EMAIL", "").strip()
    if explicit:
        return explicit
    env_allow = os.getenv("ALLOWED_EMAILS", "").strip()
    if env_allow and env_allow != "*":
        first = next((e.strip() for e in env_allow.split(",") if e.strip()), None)
        return first
    return None


def _bearer_claims(request: Request) -> dict:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed bearer token")
    try:
        return _decode(header[len("Bearer "):])
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


async def require_user(request: Request):
    if not _auth_enabled():
        return  # auth disabled (local dev / tests)

    path = request.url.path
    if path.startswith(_EXEMPT_PREFIXES) or not path.startswith("/api/"):
        return

    claims = _bearer_claims(request)
    _check_allowlist(claims.get("email"))


async def require_admin(request: Request):
    """Restrict allowlist-management endpoints to the configured admin.

    Set ADMIN_EMAIL to an explicit admin address, or leave it unset to fall back
    to the first entry in ALLOWED_EMAILS. When auth is disabled (local dev /
    tests), this is a no-op. When auth is enabled but no admin is configured
    (e.g. ALLOWED_EMAILS=*), the endpoint returns 403 — manage the allowlist
    via the env var directly in that case.
    """
    if not _auth_enabled():
        return

    admin = _get_admin_email()
    if not admin:
        raise HTTPException(
            status_code=403,
            detail="Allowlist management requires ADMIN_EMAIL (or a non-wildcard ALLOWED_EMAILS) to be set",
        )

    claims = _bearer_claims(request)
    caller = _canonical_email(claims.get("email", ""))
    if caller != _canonical_email(admin):
        raise HTTPException(status_code=403, detail="Admin access required")
