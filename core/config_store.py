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
from typing import Optional

import compliance

_CONFIG_PATH = Path(__file__).parent.parent / "config.json"
_LOCK = threading.Lock()

_DEFAULTS = {
    "github_token": "",   # legacy single-token field — superseded by github_tokens
    "github_tokens": [],  # list of {username, orgs, token}
    "webhook_secret": "",
    "repos": [],
    "llm_execution_mode": "",
    "llm_worker_secret": "",
    "llm_base_url": "",
    "llm_api_key": "",
    # New: ordered list of model slots [{label, model, provider}].
    # When set, this supersedes the legacy openrouter_model / openrouter_model_2 fields.
    "openrouter_models": [],
    # Legacy single/dual model fields — kept for backward compat with existing configs.
    "openrouter_model": "",
    "openrouter_provider": "",
    "openrouter_model_2": "",
    "openrouter_provider_2": "",
    "embedding_model": "",
    "compliance_policies": compliance.default_policies(),
    "local_review_agents": [
        {
            "id": "codex",
            "label": "Codex",
            "enabled": True,
            "command": [
                "codex",
                "exec",
                "--skip-git-repo-check",
                "--output-schema",
                "{schema_path}",
                "--output-last-message",
                "{output_path}",
                "-",
            ],
        },
        {
            "id": "kimi",
            "label": "Kimi",
            "enabled": True,
            "command": ["kimi", "-p", "{prompt}", "--output-format", "stream-json"],
        },
    ],
}

DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"
DEFAULT_EMBEDDING_MODEL = "openai/text-embedding-3-small"
DEFAULT_LLM_BASE_URL = "https://openrouter.ai/api/v1"
_LEGACY_LOCAL_AGENT_COMMANDS = {
    "kimi": [
        ["kimi"],
        ["kimi", "-"],
        ["kimi", "--yolo", "-p", "{prompt}", "--output-format", "stream-json"],
    ],
}


def _is_postgres() -> bool:
    return os.getenv("CONFIG_STORE_BACKEND", "file").lower() == "postgres"


def _merge(data: dict) -> dict:
    """Merge a raw config dict onto the defaults and normalize."""
    merged = copy.deepcopy(_DEFAULTS)
    raw_policies = data.get("compliance_policies")
    raw_agents = data.get("local_review_agents")
    for key in _DEFAULTS:
        if key == "compliance_policies":
            merged[key] = compliance.normalize_policies(raw_policies)
        elif key == "local_review_agents":
            merged[key] = _normalize_local_review_agents(raw_agents)
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
    update = dict(update or {})
    if _is_postgres():
        current = _pg_read()
        current.update(update)
        current["repos"] = [r for r in (current.get("repos") or []) if isinstance(r, str)]
        current["compliance_policies"] = compliance.normalize_policies(current.get("compliance_policies"))
        current["local_review_agents"] = _normalize_local_review_agents(current.get("local_review_agents"))
        _pg_write(current)
        return current
    with _LOCK:
        current = _read()
        current.update(update)
        current["repos"] = [r for r in (current.get("repos") or []) if isinstance(r, str)]
        current["compliance_policies"] = compliance.normalize_policies(current.get("compliance_policies"))
        current["local_review_agents"] = _normalize_local_review_agents(current.get("local_review_agents"))
        _write(current)
        return current


def _normalize_local_review_agents(raw) -> list:
    agents = []
    defaults_by_id = {item["id"]: copy.deepcopy(item) for item in _DEFAULTS["local_review_agents"]}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            agent_id = str(item.get("id") or "").strip().lower()
            if not agent_id:
                continue
            base = defaults_by_id.get(agent_id, {"id": agent_id, "label": agent_id, "enabled": True, "command": []})
            command = item.get("command")
            if isinstance(command, list):
                normalized_command = [str(v).strip() for v in command if str(v).strip()]
                if normalized_command in _LEGACY_LOCAL_AGENT_COMMANDS.get(agent_id, []):
                    normalized_command = copy.deepcopy(defaults_by_id.get(agent_id, base).get("command", []))
                base["command"] = normalized_command
            label = item.get("label")
            if isinstance(label, str) and label.strip():
                base["label"] = label.strip()
            if isinstance(item.get("enabled"), bool):
                base["enabled"] = item["enabled"]
            agents.append(base)
    seen = {item["id"] for item in agents}
    for default in _DEFAULTS["local_review_agents"]:
        if default["id"] not in seen:
            agents.append(copy.deepcopy(default))
    return agents


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


def get_llm_execution_mode() -> str:
    mode = (load_config().get("llm_execution_mode") or os.getenv("LLM_EXECUTION_MODE") or "inline").strip().lower()
    return mode if mode in {"inline", "local_queue"} else "inline"


def get_llm_worker_secret() -> str:
    return load_config().get("llm_worker_secret") or os.getenv("LLM_WORKER_SECRET", "")


def get_llm_base_url() -> str:
    value = load_config().get("llm_base_url") or os.getenv("OPENROUTER_BASE_URL") or DEFAULT_LLM_BASE_URL
    return value.strip().rstrip("/")


def get_llm_api_key() -> str:
    return load_config().get("llm_api_key") or os.getenv("OPENROUTER_API_KEY", "")


def is_openrouter_target(base_url: Optional[str] = None) -> bool:
    base = (base_url or get_llm_base_url()).strip().lower()
    return "openrouter.ai" in base


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


def get_compliance_policies() -> dict:
    cfg = load_config()
    return compliance.normalize_policies(cfg.get("compliance_policies"))


def get_compliance_policy(repo: str) -> dict:
    return compliance.policy_for_repo(get_compliance_policies(), repo)


def repo_requires_compliance_review(repo: str) -> bool:
    return bool(get_compliance_policy(repo).get("enabled"))


def get_local_review_agents() -> list:
    return _normalize_local_review_agents(load_config().get("local_review_agents"))


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
