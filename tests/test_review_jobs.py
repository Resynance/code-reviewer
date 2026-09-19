"""Tests for core/review_jobs.py queue transitions."""

from datetime import datetime, timezone
import sys

import pytest

import review_jobs


@pytest.fixture
def jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(review_jobs, "_FILE_PATH", tmp_path / "review_jobs.json")
    monkeypatch.delenv("REVIEW_STORE_BACKEND", raising=False)
    monkeypatch.delenv("CONFIG_STORE_BACKEND", raising=False)
    return review_jobs


def test_create_job_persists_type_and_executor(jobs):
    job = jobs.create_job({"repo": "org/a"}, job_type="assessment", executor="local_queue")
    assert job["status"] == "queued"
    assert job["job_type"] == "assessment"
    assert job["executor"] == "local_queue"


def test_claim_next_job_marks_running(jobs):
    first = jobs.create_job({"repo": "org/a"}, job_type="review", executor="local_queue")
    jobs.create_job({"repo": "org/b"}, job_type="assessment", executor="local_queue")
    claimed = jobs.claim_next_job(job_types=["review"], executor="local_queue", worker_id="mac")
    assert claimed["id"] == first["id"]
    assert claimed["status"] == "running"
    assert claimed["claimed_by"] == "mac"
    assert claimed["started_at"]


def test_update_job_marks_completed(jobs):
    job = jobs.create_job({"repo": "org/a"}, job_type="review", executor="local_queue")
    jobs.claim_next_job(executor="local_queue", worker_id="mac")
    updated = jobs.update_job(job["id"], status="done", result={"summary": "ok"})
    assert updated["status"] == "done"
    assert updated["result"]["summary"] == "ok"
    assert updated["completed_at"]


def test_fail_job_does_not_overwrite_completed_job(jobs):
    job = jobs.create_job({"repo": "org/a"}, job_type="review", executor="local_queue")
    jobs.claim_next_job(executor="local_queue", worker_id="mac")
    jobs.update_job(job["id"], status="done", result={"summary": "ok"})

    updated = jobs.fail_job(job["id"], "TimeoutException: response timed out")

    assert updated["status"] == "done"
    assert updated["result"]["summary"] == "ok"
    assert updated["error"] is None


def test_pg_create_auto_ensures_queue_schema(monkeypatch):
    monkeypatch.setenv("REVIEW_STORE_BACKEND", "postgres")
    monkeypatch.setattr(review_jobs, "_PG_SCHEMA_READY", False)

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
            return (
                "123",
                "review",
                "inline",
                "queued",
                {"repo": "org/a"},
                None,
                None,
                None,
                None,
                None,
                now,
                now,
            )

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

    monkeypatch.setitem(sys.modules, "db", FakeDb)

    job = review_jobs.create_job({"repo": "org/a"}, job_type="review", executor="inline")

    assert job["id"] == "123"
    assert "create table if not exists public.review_jobs" in calls[0][0].lower()
    assert "alter table public.review_jobs" in calls[1][0].lower()
    assert "create index if not exists review_jobs_queue_idx" in calls[2][0].lower()
    assert "insert into review_jobs (job_type, executor, request)" in calls[3][0].lower()


def test_pg_fail_job_filters_terminal_statuses(monkeypatch):
    monkeypatch.setenv("REVIEW_STORE_BACKEND", "postgres")
    monkeypatch.setattr(review_jobs, "_PG_SCHEMA_READY", True)

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
            sql = calls[-1][0].lower()
            if sql.startswith("update review_jobs"):
                return None
            return (
                "123",
                "review",
                "local_queue",
                "done",
                {"repo": "org/a"},
                {"summary": "ok"},
                None,
                "mac",
                now,
                now,
                now,
                now,
            )

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

    monkeypatch.setitem(sys.modules, "db", FakeDb)

    updated = review_jobs.fail_job("123", "TimeoutException: response timed out")

    assert updated["status"] == "done"
    assert updated["result"]["summary"] == "ok"
    assert "status not in ('done', 'error')" in calls[0][0].lower()
    assert calls[0][1] == ("TimeoutException: response timed out", "123")
