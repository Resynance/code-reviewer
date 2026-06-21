"""Shared pytest fixtures and path setup for the test suite."""

import os
import sys
from pathlib import Path

import pytest

# Disable ChromaDB's anonymous telemetry before chromadb is imported anywhere —
# its opentelemetry path can intermittently crash during upsert/query.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

ROOT = Path(__file__).parent
# Make the core modules importable the same way the backend does.
sys.path.insert(0, str(ROOT / "core"))

# Environment variables that act as fallbacks in the app. Cleared per-test so
# config-file values and defaults are exercised deterministically.
_ENV_VARS = [
    "GITHUB_TOKEN",
    "GITHUB_WEBHOOK_SECRET",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "OPENROUTER_PROVIDER",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIM",
    "SUPABASE_JWT_SECRET",
    "SUPABASE_URL",
    "SUPABASE_JWKS_URL",
    "ALLOWED_EMAILS",
    "CONFIG_STORE_BACKEND",
    "REVIEW_STORE_BACKEND",
    "DATABASE_URL",
]


@pytest.fixture
def clean_env(monkeypatch):
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def cfg(tmp_path, monkeypatch, clean_env):
    """config_store pointed at a temp config.json with env fallbacks cleared."""
    import config_store

    monkeypatch.setattr(config_store, "_CONFIG_PATH", tmp_path / "config.json")
    return config_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A real ChromaDecisionStore backed by a temp persist dir."""
    monkeypatch.setenv("DECISION_STORE_BACKEND", "chroma")
    monkeypatch.setenv("CHROMA_PERSIST_DIR", str(tmp_path / "chroma"))
    from decision_store import create_store

    return create_store()


@pytest.fixture
def client(tmp_path, monkeypatch, clean_env):
    """FastAPI TestClient wired to a temp config + temp Chroma store.

    Yields (TestClient, backend.main module) so tests can patch module globals
    (e.g. get_engine, run_backfill) as needed.
    """
    monkeypatch.setenv("DECISION_STORE_BACKEND", "chroma")
    monkeypatch.setenv("CHROMA_PERSIST_DIR", str(tmp_path / "chroma"))

    import config_store
    import review_store
    import review_jobs
    import assessment_store
    import access_store
    import rate_limit
    import backend.main as main
    from starlette.testclient import TestClient

    monkeypatch.setattr(config_store, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(review_store, "_FILE_PATH", tmp_path / "reviews.json")
    monkeypatch.setattr(review_jobs, "_FILE_PATH", tmp_path / "review_jobs.json")
    monkeypatch.setattr(assessment_store, "_FILE_PATH", tmp_path / "assessments.json")
    monkeypatch.setattr(access_store, "_FILE_PATH", tmp_path / "access.json")
    access_store._CACHE["emails"] = None  # reset cache between tests
    rate_limit._windows.clear()  # reset per-user sliding windows between tests
    # Reset lazily-built singletons so each test gets a fresh, temp-backed store.
    monkeypatch.setattr(main, "_store", None)
    monkeypatch.setattr(main, "_engine", None)

    return TestClient(main.app), main
