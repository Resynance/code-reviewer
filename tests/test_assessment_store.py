"""Tests for core/assessment_store.py."""

from datetime import datetime, timezone
import sys
import types

import pytest

import assessment_store


@pytest.fixture
def store(tmp_path, monkeypatch, clean_env):
    monkeypatch.setattr(assessment_store, "_FILE_PATH", tmp_path / "assessments.json")
    monkeypatch.setattr(assessment_store, "_PG_SCHEMA_READY", False)
    return assessment_store


def test_save_and_list_newest_first(store):
    store.save_assessment({"repo": "org/a", "summary": "first"})
    store.save_assessment({"repo": "org/a", "summary": "second"})
    out = store.list_assessments()
    assert [item["summary"] for item in out] == ["second", "first"]


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
            return (123, now)

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

    saved = store.save_assessment({"repo": "org/a", "summary": "ok"})

    assert saved["id"] == 123
    assert "create table if not exists public.assessments" in calls[0][0].lower()
    assert "rename column hipaa_review to compliance_review" in calls[1][0].lower()
    assert "alter table public.assessments" in calls[2][0].lower()
    assert "create index if not exists assessments_repo_idx" in calls[3][0].lower()
    assert "insert into assessments" in calls[5][0].lower()
