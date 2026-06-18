"""
config_store.py — server-side persisted settings.

Holds the GitHub token, webhook secret, and the list of configured repositories
in a JSON file at the project root (config.json, gitignored). The frontend
Settings page reads and writes these through the API, so no .env editing is
required. Environment variables remain as fallback defaults.

Secrets live in plaintext on disk — appropriate for this local, single-user
tool. Never echo the raw token/secret back to the client; expose booleans only.
"""

import os
import json
import threading
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent.parent / "config.json"
_LOCK = threading.Lock()

_DEFAULTS = {
    "github_token": "",
    "webhook_secret": "",
    "repos": [],
    "openrouter_model": "",
    "openrouter_provider": "",
}

DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"


def _read():
    """Read config.json, falling back to defaults on missing/corrupt file."""
    if not _CONFIG_PATH.exists():
        return dict(_DEFAULTS)
    try:
        with open(_CONFIG_PATH) as f:
            data = json.load(f)
    except (ValueError, OSError):
        return dict(_DEFAULTS)
    merged = dict(_DEFAULTS)
    for key in _DEFAULTS:
        merged[key] = data.get(key, merged[key])
    merged["repos"] = [r for r in (merged.get("repos") or []) if isinstance(r, str)]
    return merged


def _write(data):
    """Write the full config atomically (temp file + replace)."""
    tmp = _CONFIG_PATH.with_name(_CONFIG_PATH.name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(_CONFIG_PATH)


def load_config():
    with _LOCK:
        return _read()


def save_config(update: dict):
    """Merge `update` into the stored config and persist. Returns the new config."""
    with _LOCK:
        current = _read()
        current.update(update)
        current["repos"] = [r for r in (current.get("repos") or []) if isinstance(r, str)]
        _write(current)
        return current


def get_github_token() -> str:
    return load_config().get("github_token") or os.getenv("GITHUB_TOKEN", "")


def get_webhook_secret() -> str:
    return load_config().get("webhook_secret") or os.getenv("GITHUB_WEBHOOK_SECRET", "")


def get_repos() -> list:
    return load_config().get("repos", [])


def get_model() -> str:
    return load_config().get("openrouter_model") or os.getenv("OPENROUTER_MODEL") or DEFAULT_MODEL


def get_provider() -> str:
    """Optional OpenRouter provider to pin (e.g. 'Anthropic'). Empty = auto-route."""
    return load_config().get("openrouter_provider") or os.getenv("OPENROUTER_PROVIDER") or ""


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
