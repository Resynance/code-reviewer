"""
review_jobs.py — async review jobs (status + result).

Lets the UI enqueue a review and poll for the result instead of holding one long
HTTP request open while the model runs. On Vercel a synchronous review could
exceed the function time limit (maxDuration) and be killed with a 504; an
enqueue + poll flow keeps each request short.

Two backends, selected like review_store (REVIEW_STORE_BACKEND, falling back to
CONFIG_STORE_BACKEND): "postgres" → public.review_jobs via core/db.py; else a
local JSON file (review_jobs.json at the project root, gitignored).

A job moves: queued → running → (done | error).
"""

import os
import json
import uuid
import threading
from pathlib import Path
from datetime import datetime, timezone

_FILE_PATH = Path(__file__).parent.parent / "review_jobs.json"
_LOCK = threading.Lock()

# Columns mirror the postgres table; the file backend stores the same shape.
_FIELDS = ("id", "status", "request", "result", "error", "created_at", "updated_at")


def _backend() -> str:
    return os.getenv("REVIEW_STORE_BACKEND") or os.getenv("CONFIG_STORE_BACKEND", "file")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(request: dict) -> dict:
    """Create a queued job holding the review request payload."""
    if _backend() == "postgres":
        return _pg_create(request)
    return _file_create(request)


def get_job(job_id: str):
    """Return the job dict, or None if no job has that id."""
    if _backend() == "postgres":
        return _pg_get(job_id)
    return _file_get(job_id)


def update_job(job_id: str, status=None, result=None, error=None):
    """Patch the given fields (only non-None ones) and bump updated_at."""
    if _backend() == "postgres":
        return _pg_update(job_id, status, result, error)
    return _file_update(job_id, status, result, error)


# ----- file backend ----- #

def _file_read():
    if not _FILE_PATH.exists():
        return []
    try:
        with open(_FILE_PATH) as f:
            return json.load(f)
    except (ValueError, OSError):
        return []


def _file_write(jobs):
    tmp = _FILE_PATH.with_name(_FILE_PATH.name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(jobs, f, indent=2)
    tmp.replace(_FILE_PATH)


def _file_create(request):
    with _LOCK:
        jobs = _file_read()
        job = {
            "id": str(uuid.uuid4()),
            "status": "queued",
            "request": request,
            "result": None,
            "error": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        jobs.append(job)
        _file_write(jobs)
        return job


def _file_get(job_id):
    for job in _file_read():
        if job.get("id") == job_id:
            return job
    return None


def _file_update(job_id, status, result, error):
    with _LOCK:
        jobs = _file_read()
        for job in jobs:
            if job.get("id") == job_id:
                if status is not None:
                    job["status"] = status
                if result is not None:
                    job["result"] = result
                if error is not None:
                    job["error"] = error
                job["updated_at"] = _now()
                _file_write(jobs)
                return job
    return None


# ----- postgres backend ----- #

_SELECT = (
    "SELECT id, status, request, result, error, created_at, updated_at "
    "FROM review_jobs"
)


def _row_to_job(row):
    job = dict(zip(_FIELDS, row))
    job["id"] = str(job["id"])
    for k in ("created_at", "updated_at"):
        if hasattr(job.get(k), "isoformat"):
            job[k] = job[k].isoformat()
    return job


def _pg_create(request):
    import db

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO review_jobs (request) VALUES (%s) "
            "RETURNING id, status, request, result, error, created_at, updated_at",
            (json.dumps(request),),
        )
        return _row_to_job(cur.fetchone())


def _pg_get(job_id):
    import db

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(_SELECT + " WHERE id = %s", (job_id,))
        row = cur.fetchone()
    return _row_to_job(row) if row else None


def _pg_update(job_id, status, result, error):
    import db

    sets, params = [], []
    if status is not None:
        sets.append("status = %s")
        params.append(status)
    if result is not None:
        sets.append("result = %s")
        params.append(json.dumps(result))
    if error is not None:
        sets.append("error = %s")
        params.append(error)
    sets.append("updated_at = now()")
    params.append(job_id)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE review_jobs SET " + ", ".join(sets) + " WHERE id = %s "
            "RETURNING id, status, request, result, error, created_at, updated_at",
            params,
        )
        row = cur.fetchone()
    return _row_to_job(row) if row else None
