"""Tests for core/config_store.py — persistence, env fallback, repo management."""

import os


def test_defaults_when_no_file(cfg):
    c = cfg.load_config()
    assert c == {
        "github_token": "",
        "github_tokens": [],
        "webhook_secret": "",
        "repos": [],
        "openrouter_model": "",
        "openrouter_provider": "",
        "openrouter_model_2": "",
        "openrouter_provider_2": "",
        "embedding_model": "",
    }


def test_embedding_model_default_and_config(cfg, monkeypatch):
    assert cfg.get_embedding_model() == cfg.DEFAULT_EMBEDDING_MODEL
    monkeypatch.setenv("EMBEDDING_MODEL", "env/embed")
    assert cfg.get_embedding_model() == "env/embed"
    cfg.save_config({"embedding_model": "cfg/embed"})
    assert cfg.get_embedding_model() == "cfg/embed"


def test_postgres_backend_dispatch(cfg, monkeypatch):
    # With CONFIG_STORE_BACKEND=postgres, load/save route to the pg helpers
    # (here backed by an in-memory dict) and the public API works unchanged.
    import config_store
    store = {"data": {}}
    monkeypatch.setenv("CONFIG_STORE_BACKEND", "postgres")
    monkeypatch.setattr(config_store, "_pg_read", lambda: config_store._merge(store["data"]))
    monkeypatch.setattr(config_store, "_pg_write", lambda data: store.__setitem__("data", data))

    cfg.save_config({"github_token": "t", "repos": ["org/a"]})
    assert store["data"]["github_token"] == "t"
    assert cfg.get_github_token() == "t"
    assert cfg.get_repos() == ["org/a"]

    cfg.add_repo("org/b")
    assert set(cfg.get_repos()) == {"org/a", "org/b"}


def test_save_and_load_roundtrip(cfg):
    cfg.save_config({"github_token": "ghp_x", "repos": ["org/a"]})
    c = cfg.load_config()
    assert c["github_token"] == "ghp_x"
    assert c["repos"] == ["org/a"]


def test_save_is_partial_merge(cfg):
    cfg.save_config({"github_token": "t1", "webhook_secret": "s1"})
    cfg.save_config({"github_token": "t2"})  # only token changes
    c = cfg.load_config()
    assert c["github_token"] == "t2"
    assert c["webhook_secret"] == "s1"


def test_corrupt_file_falls_back_to_defaults(cfg):
    cfg._CONFIG_PATH.write_text("{not valid json")
    assert cfg.load_config()["repos"] == []


def test_repos_normalized_to_strings(cfg):
    cfg._CONFIG_PATH.write_text('{"repos": ["org/a", 5, null, "org/b"]}')
    assert cfg.get_repos() == ["org/a", "org/b"]


def test_token_falls_back_to_env(cfg, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    assert cfg.get_github_token() == "env-token"


def test_config_token_overrides_env(cfg, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    cfg.save_config({"github_token": "config-token"})
    assert cfg.get_github_token() == "config-token"


def test_webhook_secret_fallback(cfg, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "env-secret")
    assert cfg.get_webhook_secret() == "env-secret"
    cfg.save_config({"webhook_secret": "cfg-secret"})
    assert cfg.get_webhook_secret() == "cfg-secret"


def test_model_default_when_unset(cfg):
    assert cfg.get_model() == cfg.DEFAULT_MODEL


def test_model_env_then_config(cfg, monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL", "env/model")
    assert cfg.get_model() == "env/model"
    cfg.save_config({"openrouter_model": "cfg/model"})
    assert cfg.get_model() == "cfg/model"


def test_provider_default_empty(cfg):
    assert cfg.get_provider() == ""
    cfg.save_config({"openrouter_provider": "Anthropic"})
    assert cfg.get_provider() == "Anthropic"


def test_add_repo_dedupes(cfg):
    cfg.add_repo("org/a")
    cfg.add_repo("org/a")
    cfg.add_repo("org/b")
    assert cfg.get_repos() == ["org/a", "org/b"]


def test_remove_repo(cfg):
    cfg.save_config({"repos": ["org/a", "org/b"]})
    assert cfg.remove_repo("org/a") == ["org/b"]
    assert cfg.get_repos() == ["org/b"]


def test_save_writes_to_disk(cfg):
    cfg.save_config({"github_token": "persisted"})
    assert cfg._CONFIG_PATH.exists()
    assert "persisted" in cfg._CONFIG_PATH.read_text()


# ----- multi-token support ----- #

def test_add_and_get_github_tokens(cfg):
    assert cfg.get_github_tokens() == []
    cfg.add_github_token("alice", ["acme"], "tok_alice")
    cfg.add_github_token("bob", [], "tok_bob")
    tokens = cfg.get_github_tokens()
    assert len(tokens) == 2
    assert tokens[0] == {"username": "alice", "orgs": ["acme"], "token": "tok_alice"}
    assert tokens[1] == {"username": "bob", "orgs": [], "token": "tok_bob"}


def test_add_github_token_replaces_same_username(cfg):
    cfg.add_github_token("alice", ["acme"], "tok_old")
    cfg.add_github_token("alice", ["acme", "globex"], "tok_new")
    tokens = cfg.get_github_tokens()
    assert len(tokens) == 1
    assert tokens[0]["token"] == "tok_new"
    assert tokens[0]["orgs"] == ["acme", "globex"]


def test_remove_github_token(cfg):
    cfg.add_github_token("alice", [], "tok_a")
    cfg.add_github_token("bob", [], "tok_b")
    remaining = cfg.remove_github_token("alice")
    assert len(remaining) == 1
    assert remaining[0]["username"] == "bob"


def test_get_token_for_matches_username(cfg):
    cfg.add_github_token("alice", ["acme"], "tok_alice")
    cfg.add_github_token("bob", ["globex"], "tok_bob")
    assert cfg.get_token_for("alice") == "tok_alice"
    assert cfg.get_token_for("bob") == "tok_bob"


def test_get_token_for_matches_org(cfg):
    cfg.add_github_token("alice", ["acme", "startup"], "tok_alice")
    assert cfg.get_token_for("acme") == "tok_alice"
    assert cfg.get_token_for("startup") == "tok_alice"


def test_get_token_for_falls_back_to_first(cfg):
    cfg.add_github_token("alice", ["acme"], "tok_alice")
    # "unknown-org" not in any token's list → first token
    assert cfg.get_token_for("unknown-org") == "tok_alice"


def test_get_token_for_returns_none_when_empty(cfg):
    assert cfg.get_token_for("anything") is None


def test_get_github_token_reads_from_tokens_list(cfg):
    cfg.add_github_token("alice", [], "tok_alice")
    assert cfg.get_github_token() == "tok_alice"


def test_legacy_github_token_fallback(cfg, monkeypatch):
    # Old-style single token (no github_tokens list) still works via get_github_token
    cfg.save_config({"github_token": "old_token"})
    assert cfg.get_github_token() == "old_token"
