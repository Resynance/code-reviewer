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
_PG_SCHEMA_LOCK = threading.Lock()
_PG_SCHEMA_READY = False

# Columns mirror the postgres table; the file backend stores the same shape.
_FIELDS = (
    "id", "job_type", "executor", "status", "request", "result", "error",
    "claimed_by", "started_at", "completed_at", "created_at", "updated_at",
)


def _backend() -> str:
    return os.getenv("REVIEW_STORE_BACKEND") or os.getenv("CONFIG_STORE_BACKEND", "file")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(request: dict, job_type: str = "review", executor: str = "inline") -> dict:
    """Create a queued job holding the review request payload."""
    if _backend() == "postgres":
        return _pg_create(request, job_type, executor)
    return _file_create(request, job_type, executor)


def get_job(job_id: str):
    """Return the job dict, or None if no job has that id."""
    if _backend() == "postgres":
        return _pg_get(job_id)
    return _file_get(job_id)


def list_jobs(limit: int = 100, executor: str = "", status: str = "", job_type: str = "") -> list:
    """List jobs newest-first, optionally filtered."""
    if _backend() == "postgres":
        return _pg_list(limit, executor, status, job_type)
    return _file_list(limit, executor, status, job_type)


def claim_next_job(job_types=None, executor: str = "local_queue", worker_id: str = ""):
    """Claim the next queued job for an external worker, or None if none exist."""
    job_types = tuple(job_types or ())
    if _backend() == "postgres":
        return _pg_claim_next(job_types, executor, worker_id)
    return _file_claim_next(job_types, executor, worker_id)


def claim_next_agentic_review_job(executor: str = "local_queue", worker_id: str = ""):
    """Claim the next queued agentic review job, or None if none exist."""
    if _backend() == "postgres":
        return _pg_claim_next_agentic_review(executor, worker_id)
    return _file_claim_next_agentic_review(executor, worker_id)


def update_job(job_id: str, status=None, result=None, error=None, claimed_by=None):
    """Patch the given fields (only non-None ones) and bump updated_at."""
    if _backend() == "postgres":
        return _pg_update(job_id, status, result, error, claimed_by)
    return _file_update(job_id, status, result, error, claimed_by)


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


def _file_create(request, job_type, executor):
    with _LOCK:
        jobs = _file_read()
        job = {
            "id": str(uuid.uuid4()),
            "job_type": job_type,
            "executor": executor,
            "status": "queued",
            "request": request,
            "result": None,
            "error": None,
            "claimed_by": None,
            "started_at": None,
            "completed_at": None,
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


def _file_list(limit, executor, status, job_type):
    jobs = list(reversed(_file_read()))
    out = []
    for job in jobs:
        if executor and job.get("executor") != executor:
            continue
        if status and job.get("status") != status:
            continue
        if job_type and job.get("job_type") != job_type:
            continue
        out.append(job)
        if len(out) >= limit:
            break
    return out


def _file_claim_next(job_types, executor, worker_id):
    with _LOCK:
        jobs = _file_read()
        for job in jobs:
            if job.get("status") != "queued" or job.get("executor") != executor:
                continue
            if job_types and job.get("job_type") not in job_types:
                continue
            job["status"] = "running"
            job["claimed_by"] = worker_id or None
            job["started_at"] = _now()
            job["updated_at"] = _now()
            _file_write(jobs)
            return job
    return None


def _file_claim_next_agentic_review(executor, worker_id):
    with _LOCK:
        jobs = _file_read()
        for job in jobs:
            req = job.get("request") or {}
            if job.get("status") != "queued" or job.get("executor") != executor:
                continue
            if job.get("job_type") != "review" or not req.get("agentic"):
                continue
            job["status"] = "running"
            job["claimed_by"] = worker_id or None
            job["started_at"] = _now()
            job["updated_at"] = _now()
            _file_write(jobs)
            return job
    return None


def _file_update(job_id, status, result, error, claimed_by):
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
                if claimed_by is not None:
                    job["claimed_by"] = claimed_by or None
                if status == "running" and not job.get("started_at"):
                    job["started_at"] = _now()
                if status in {"done", "error"}:
                    job["completed_at"] = _now()
                job["updated_at"] = _now()
                _file_write(jobs)
                return job
    return None


# ----- postgres backend ----- #

_SELECT = (
    "SELECT id, job_type, executor, status, request, result, error, "
    "claimed_by, started_at, completed_at, created_at, updated_at "
    "FROM review_jobs"
)


def _row_to_job(row):
    job = dict(zip(_FIELDS, row))
    job["id"] = str(job["id"])
    for k in ("started_at", "completed_at", "created_at", "updated_at"):
        if hasattr(job.get(k), "isoformat"):
            job[k] = job[k].isoformat()
    return job


def _pg_ensure_schema():
    """Create/upgrade the review_jobs table in-place for older deployments."""
    global _PG_SCHEMA_READY
    if _PG_SCHEMA_READY:
        return
    import db

    with _PG_SCHEMA_LOCK:
        if _PG_SCHEMA_READY:
            return
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS public.review_jobs ("
                "  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
                "  status text NOT NULL DEFAULT 'queued',"
                "  request jsonb NOT NULL,"
                "  result jsonb,"
                "  error text,"
                "  created_at timestamptz NOT NULL DEFAULT now(),"
                "  updated_at timestamptz NOT NULL DEFAULT now()"
                ")"
            )
            cur.execute(
                "ALTER TABLE public.review_jobs "
                "ADD COLUMN IF NOT EXISTS job_type text NOT NULL DEFAULT 'review', "
                "ADD COLUMN IF NOT EXISTS executor text NOT NULL DEFAULT 'inline', "
                "ADD COLUMN IF NOT EXISTS claimed_by text, "
                "ADD COLUMN IF NOT EXISTS started_at timestamptz, "
                "ADD COLUMN IF NOT EXISTS completed_at timestamptz"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS review_jobs_queue_idx "
                "ON public.review_jobs (executor, status, created_at)"
            )
        _PG_SCHEMA_READY = True


def _pg_create(request, job_type, executor):
    import db

    _pg_ensure_schema()
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO review_jobs (job_type, executor, request) VALUES (%s, %s, %s) "
            "RETURNING id, job_type, executor, status, request, result, error, "
            "claimed_by, started_at, completed_at, created_at, updated_at",
            (job_type, executor, json.dumps(request)),
        )
        return _row_to_job(cur.fetchone())


def _pg_get(job_id):
    import db

    _pg_ensure_schema()
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(_SELECT + " WHERE id = %s", (job_id,))
        row = cur.fetchone()
    return _row_to_job(row) if row else None


def _pg_list(limit, executor, status, job_type):
    import db

    _pg_ensure_schema()
    where = []
    params = []
    if executor:
        where.append("executor = %s")
        params.append(executor)
    if status:
        where.append("status = %s")
        params.append(status)
    if job_type:
        where.append("job_type = %s")
        params.append(job_type)
    params.append(limit)
    sql = _SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT %s"
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [_row_to_job(row) for row in rows]


def _pg_claim_next(job_types, executor, worker_id):
    import db

    _pg_ensure_schema()
    where = ["status = 'queued'", "executor = %s"]
    params = [executor]
    if job_types:
        where.append("job_type = ANY(%s)")
        params.append(list(job_types))
    params.append(worker_id or None)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "WITH next_job AS ("
            "  SELECT id FROM review_jobs WHERE " + " AND ".join(where) + " "
            "  ORDER BY created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED"
            ") "
            "UPDATE review_jobs j SET status = 'running', claimed_by = %s, "
            "started_at = COALESCE(j.started_at, now()), updated_at = now() "
            "FROM next_job WHERE j.id = next_job.id "
            "RETURNING id, job_type, executor, status, request, result, error, "
            "claimed_by, started_at, completed_at, created_at, updated_at",
            params,
        )
        row = cur.fetchone()
    return _row_to_job(row) if row else None


def _pg_claim_next_agentic_review(executor, worker_id):
    import db

    _pg_ensure_schema()
    params = [executor, worker_id or None]
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "WITH next_job AS ("
            "  SELECT id FROM review_jobs "
            "  WHERE status = 'queued' AND executor = %s AND job_type = 'review' "
            "    AND COALESCE((request->>'agentic')::boolean, false) = true "
            "  ORDER BY created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED"
            ") "
            "UPDATE review_jobs j SET status = 'running', claimed_by = %s, "
            "started_at = COALESCE(j.started_at, now()), updated_at = now() "
            "FROM next_job WHERE j.id = next_job.id "
            "RETURNING id, job_type, executor, status, request, result, error, "
            "claimed_by, started_at, completed_at, created_at, updated_at",
            params,
        )
        row = cur.fetchone()
    return _row_to_job(row) if row else None


def _pg_update(job_id, status, result, error, claimed_by):
    import db

    _pg_ensure_schema()
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
    if claimed_by is not None:
        sets.append("claimed_by = %s")
        params.append(claimed_by or None)
    if status == "running":
        sets.append("started_at = COALESCE(started_at, now())")
    if status in {"done", "error"}:
        sets.append("completed_at = now()")
    sets.append("updated_at = now()")
    params.append(job_id)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE review_jobs SET " + ", ".join(sets) + " WHERE id = %s "
            "RETURNING id, job_type, executor, status, request, result, error, "
            "claimed_by, started_at, completed_at, created_at, updated_at",
            params,
        )
        row = cur.fetchone()
    return _row_to_job(row) if row else None
