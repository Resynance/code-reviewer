"""
config_store.py — server-side persisted settings.

Holds the GitHub token, webhook secret, repo list, and model settings. Two
backends, selected by CONFIG_STORE_BACKEND:
  - "file" (default): a JSON file at the project root (config.json, gitignored).
  - "postgres": a single-row app_settings(id=1, data jsonb) table — used on
    Vercel, where the filesystem is read-only.
Environment variables remain as fallback defaults.

Secrets live in plaintext on disk — appropriate for this local, single-user
tool. Never echo the raw token/secret back to the client; expose booleans only.
"""

import os
import json
import threading
import copy
from pathlib import Path

import hipaa

_CONFIG_PATH = Path(__file__).parent.parent / "config.json"
_LOCK = threading.Lock()

_DEFAULTS = {
    "github_token": "",   # legacy single-token field — superseded by github_tokens
    "github_tokens": [],  # list of {username, orgs, token}
    "webhook_secret": "",
    "repos": [],
    # New: ordered list of model slots [{label, model, provider}].
    # When set, this supersedes the legacy openrouter_model / openrouter_model_2 fields.
    "openrouter_models": [],
    # Legacy single/dual model fields — kept for backward compat with existing configs.
    "openrouter_model": "",
    "openrouter_provider": "",
    "openrouter_model_2": "",
    "openrouter_provider_2": "",
    "embedding_model": "",
    "hipaa_policies": hipaa.default_policies(),
}

DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"
DEFAULT_EMBEDDING_MODEL = "openai/text-embedding-3-small"


def _is_postgres() -> bool:
    return os.getenv("CONFIG_STORE_BACKEND", "file").lower() == "postgres"


def _merge(data: dict) -> dict:
    """Merge a raw config dict onto the defaults and normalize."""
    merged = copy.deepcopy(_DEFAULTS)
    for key in _DEFAULTS:
        if key == "hipaa_policies":
            merged[key] = hipaa.normalize_policies(data.get(key))
        else:
            merged[key] = data.get(key, merged[key])
    merged["repos"] = [r for r in (merged.get("repos") or []) if isinstance(r, str)]
    return merged


# ----- file backend ----- #

def _read():
    """Read config.json, falling back to defaults on missing/corrupt file."""
    if not _CONFIG_PATH.exists():
        return dict(_DEFAULTS)
    try:
        with open(_CONFIG_PATH) as f:
            data = json.load(f)
    except (ValueError, OSError):
        return dict(_DEFAULTS)
    return _merge(data)


def _write(data):
    """Write the full config atomically (temp file + replace)."""
    tmp = _CONFIG_PATH.with_name(_CONFIG_PATH.name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(_CONFIG_PATH)


# ----- postgres backend (single-row app_settings JSONB) ----- #

def _pg_read():
    import db

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT data FROM app_settings WHERE id = 1")
        row = cur.fetchone()
    return _merge(row[0] if row and row[0] else {})


def _pg_write(data):
    import db

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO app_settings (id, data) VALUES (1, %s) "
            "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
            (json.dumps(data),),
        )


def load_config():
    if _is_postgres():
        return _pg_read()
    with _LOCK:
        return _read()


def save_config(update: dict):
    """Merge `update` into the stored config and persist. Returns the new config."""
    if _is_postgres():
        current = _pg_read()
        current.update(update)
        current["repos"] = [r for r in (current.get("repos") or []) if isinstance(r, str)]
        current["hipaa_policies"] = hipaa.normalize_policies(current.get("hipaa_policies"))
        _pg_write(current)
        return current
    with _LOCK:
        current = _read()
        current.update(update)
        current["repos"] = [r for r in (current.get("repos") or []) if isinstance(r, str)]
        current["hipaa_policies"] = hipaa.normalize_policies(current.get("hipaa_policies"))
        _write(current)
        return current


def get_github_tokens() -> list:
    """Return all configured tokens as [{username, orgs, token}]."""
    return load_config().get("github_tokens", [])


def get_github_token() -> str:
    """Return the first token for backward compatibility."""
    tokens = get_github_tokens()
    if tokens:
        return tokens[0]["token"]
    return load_config().get("github_token") or os.getenv("GITHUB_TOKEN", "")


def get_token_for(owner: str) -> "str | None":
    """Return the best token for a given GitHub owner (username or org).

    Matches by username first, then by org membership. Falls back to the
    first token if no explicit match (handles single-token deployments and
    repos whose owner wasn't seen at token-add time).
    """
    tokens = get_github_tokens()
    for entry in tokens:
        if owner == entry.get("username") or owner in entry.get("orgs", []):
            return entry["token"]
    if tokens:
        return tokens[0]["token"]
    # Legacy fallback
    return load_config().get("github_token") or os.getenv("GITHUB_TOKEN", "") or None


def add_github_token(username: str, orgs: list, token: str) -> list:
    """Add or replace a token entry (matched by username). Returns the full list."""
    tokens = [t for t in get_github_tokens() if t.get("username") != username]
    tokens.append({"username": username, "orgs": orgs, "token": token})
    save_config({"github_tokens": tokens})
    return tokens


def remove_github_token(username: str) -> list:
    """Remove a token by GitHub username. Returns the remaining list."""
    tokens = [t for t in get_github_tokens() if t.get("username") != username]
    save_config({"github_tokens": tokens})
    return tokens


def get_webhook_secret() -> str:
    return load_config().get("webhook_secret") or os.getenv("GITHUB_WEBHOOK_SECRET", "")


def get_repos() -> list:
    return load_config().get("repos", [])


def get_models() -> list:
    """Return the ordered list of model slots as [{label, model, provider}].

    When openrouter_models is configured, it is the source of truth.
    Falls back to the legacy openrouter_model / openrouter_model_2 fields so
    that existing deployments work without reconfiguration.
    """
    cfg = load_config()
    models = cfg.get("openrouter_models") or []
    if models:
        return models
    # Legacy fallback: build a list from the old two-slot fields.
    m1 = cfg.get("openrouter_model") or os.getenv("OPENROUTER_MODEL") or DEFAULT_MODEL
    p1 = cfg.get("openrouter_provider") or os.getenv("OPENROUTER_PROVIDER") or ""
    result = [{"label": "Default", "model": m1, "provider": p1}]
    m2 = cfg.get("openrouter_model_2") or ""
    if m2:
        p2 = cfg.get("openrouter_provider_2") or ""
        result.append({"label": "Model 2", "model": m2, "provider": p2})
    return result


def get_model() -> str:
    """Return the model string for the first slot (used by the review engine)."""
    models = get_models()
    return models[0]["model"] if models else DEFAULT_MODEL


def get_provider() -> str:
    """Return the provider for the first slot. Empty string = auto-route."""
    models = get_models()
    return (models[0].get("provider") or "") if models else ""


def get_model_2() -> str:
    models = get_models()
    return models[1]["model"] if len(models) > 1 else ""


def get_provider_2() -> str:
    models = get_models()
    return (models[1].get("provider") or "") if len(models) > 1 else ""


def get_embedding_model() -> str:
    return load_config().get("embedding_model") or os.getenv("EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL


def get_hipaa_policies() -> dict:
    return hipaa.normalize_policies(load_config().get("hipaa_policies"))


def get_hipaa_policy(repo: str) -> dict:
    return hipaa.policy_for_repo(get_hipaa_policies(), repo)


def add_repo(repo: str) -> list:
    repos = get_repos()
    if repo not in repos:
        repos = repos + [repo]
        save_config({"repos": repos})
    return repos


def remove_repo(repo: str) -> list:
    repos = [r for r in get_repos() if r != repo]
    save_config({"repos": repos})
    return repos
