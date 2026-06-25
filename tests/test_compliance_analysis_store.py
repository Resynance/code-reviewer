"""Tests for core/compliance_analysis_store.py."""

from datetime import datetime, timezone
import sys
import types

import pytest

import compliance_analysis_store


@pytest.fixture
def store(tmp_path, monkeypatch, clean_env):
    monkeypatch.setattr(compliance_analysis_store, "_FILE_PATH", tmp_path / "compliance_analysis.json")
    monkeypatch.setattr(compliance_analysis_store, "_PG_SCHEMA_READY", False)
    return compliance_analysis_store


def test_save_and_get(store):
    saved = store.save_analysis({"repo": "org/a", "health": {"score": 90}})
    fetched = store.get_analysis(saved["id"])
    assert fetched["repo"] == "org/a"
    assert fetched["health"]["score"] == 90


def test_save_and_list(store):
    saved = store.save_analysis({
        "repo": "acme/app",
        "health": {"score": 90},
        "coverage": {"coverage_score": 75},
        "suggestions": [{"type": "enable_compliance"}],
    })
    assert saved["id"] == 1
    assert saved["repo"] == "acme/app"
    assert "created_at" in saved

    listed = store.list_analyses()
    assert len(listed) == 1
    assert listed[0]["health"]["score"] == 90


def test_list_filters_by_repo(store):
    store.save_analysis({"repo": "acme/app", "health": {}, "coverage": {}, "suggestions": []})
    store.save_analysis({"repo": "acme/other", "health": {}, "coverage": {}, "suggestions": []})

    listed = store.list_analyses(repo="acme/app")
    assert len(listed) == 1
    assert listed[0]["repo"] == "acme/app"


def test_get_missing_returns_none(store):
    assert store.get_analysis(9999) is None


def test_save_normalizes_empty_fields(store):
    saved = store.save_analysis({"repo": "acme/app"})
    assert saved["health"] == {}
    assert saved["coverage"] == {}
    assert saved["suggestions"] == []


def test_postgres_backend_auto_ensures_schema(store, monkeypatch):
    monkeypatch.setenv("REVIEW_STORE_BACKEND", "postgres")

    now = datetime.now(timezone.utc)
    calls = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=None):
            calls.append((sql, params))

        def fetchone(self):
            return (55, now)

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return FakeCursor()

    class FakeDb:
        @staticmethod
        def connect():
            return FakeConn()

    psycopg_mod = types.ModuleType("psycopg")
    psycopg_types_mod = types.ModuleType("psycopg.types")
    psycopg_json_mod = types.ModuleType("psycopg.types.json")
    psycopg_json_mod.Jsonb = lambda value: value

    monkeypatch.setitem(sys.modules, "db", FakeDb)
    monkeypatch.setitem(sys.modules, "psycopg", psycopg_mod)
    monkeypatch.setitem(sys.modules, "psycopg.types", psycopg_types_mod)
    monkeypatch.setitem(sys.modules, "psycopg.types.json", psycopg_json_mod)

    saved = store.save_analysis({"repo": "org/a", "health": {"score": 90}})

    assert saved["id"] == 55
    assert "create table if not exists public.compliance_analysis" in calls[0][0].lower()
    assert "alter table public.compliance_analysis" in calls[1][0].lower()
    assert "create index if not exists compliance_analysis_repo_idx" in calls[2][0].lower()
    assert "insert into compliance_analysis" in calls[4][0].lower()
