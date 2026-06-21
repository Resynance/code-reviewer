"""Tests for core/review_jobs.py queue transitions."""

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
