"""
assessment_store.py — persist project assessment results.

Two backends (postgres / file), selected by REVIEW_STORE_BACKEND (or
CONFIG_STORE_BACKEND), mirroring the pattern in review_store.py.
"""

import os
import json
import threading
from pathlib import Path
from datetime import datetime, timezone

_FILE_PATH = Path(__file__).parent.parent / "assessments.json"
_LOCK = threading.Lock()
_PG_SCHEMA_LOCK = threading.Lock()
_PG_SCHEMA_READY = False

_FIELDS = (
    "repo", "summary", "purpose",
    "tech_stack", "key_components", "vulnerabilities", "compliance_review", "model",
)

_PG_COLS = [
    "id", "repo", "summary", "purpose",
    "tech_stack", "key_components", "vulnerabilities", "compliance_review", "model", "created_at",
]


def _backend() -> str:
    return os.getenv("REVIEW_STORE_BACKEND") or os.getenv("CONFIG_STORE_BACKEND", "file")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(record: dict) -> dict:
    rec = {k: record.get(k) for k in _FIELDS}
    for k in ("tech_stack", "key_components", "vulnerabilities"):
        rec[k] = rec.get(k) or []
    rec["compliance_review"] = rec.get("compliance_review") or {}
    return rec


def save_assessment(record: dict) -> dict:
    rec = _normalize(record)
    if _backend() == "postgres":
        return _pg_save(rec)
    return _file_save(rec)


def list_assessments(repo: "str | None" = None, limit: int = 20) -> list:
    if _backend() == "postgres":
        return _pg_list(repo, limit)
    return _file_list(repo, limit)


# ----- file backend ----- #

def _file_read():
    if not _FILE_PATH.exists():
        return []
    try:
        with open(_FILE_PATH) as f:
            data = json.load(f)
    except (ValueError, OSError):
        return []
    out = []
    for row in data:
        rec = dict(row)
        rec["compliance_review"] = rec.get("compliance_review") or {}
        out.append(rec)
    return out


def _file_save(rec):
    with _LOCK:
        data = _file_read()
        rec = dict(rec)
        rec["id"] = len(data) + 1
        rec["created_at"] = _now()
        data.append(rec)
        tmp = _FILE_PATH.with_name(_FILE_PATH.name + ".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        tmp.replace(_FILE_PATH)
        return rec


def _file_list(repo, limit):
    data = _file_read()
    if repo:
        data = [r for r in data if r.get("repo") == repo]
    return list(reversed(data))[:limit]


# ----- postgres backend ----- #

def _pg_ensure_schema():
    """Create/upgrade the assessments table in-place for older deployments."""
    global _PG_SCHEMA_READY
    if _PG_SCHEMA_READY:
        return
    import db

    with _PG_SCHEMA_LOCK:
        if _PG_SCHEMA_READY:
            return
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS public.assessments ("
                "  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,"
                "  repo text NOT NULL,"
                "  summary text,"
                "  purpose text,"
                "  tech_stack jsonb NOT NULL DEFAULT '[]'::jsonb,"
                "  key_components jsonb NOT NULL DEFAULT '[]'::jsonb,"
                "  vulnerabilities jsonb NOT NULL DEFAULT '[]'::jsonb,"
                "  compliance_review jsonb NOT NULL DEFAULT '{}'::jsonb,"
                "  model text,"
                "  created_at timestamptz NOT NULL DEFAULT now()"
                ")"
            )
            cur.execute(
                "DO $$ "
                "BEGIN "
                "  IF EXISTS ("
                "    SELECT 1 FROM information_schema.columns "
                "    WHERE table_schema = 'public' "
                "      AND table_name = 'assessments' "
                "      AND column_name = 'hipaa_review'"
                "  ) AND NOT EXISTS ("
                "    SELECT 1 FROM information_schema.columns "
                "    WHERE table_schema = 'public' "
                "      AND table_name = 'assessments' "
                "      AND column_name = 'compliance_review'"
                "  ) THEN "
                "    ALTER TABLE public.assessments RENAME COLUMN hipaa_review TO compliance_review; "
                "  END IF; "
                "END $$;"
            )
            cur.execute(
                "ALTER TABLE public.assessments "
                "ADD COLUMN IF NOT EXISTS tech_stack jsonb NOT NULL DEFAULT '[]'::jsonb, "
                "ADD COLUMN IF NOT EXISTS key_components jsonb NOT NULL DEFAULT '[]'::jsonb, "
                "ADD COLUMN IF NOT EXISTS vulnerabilities jsonb NOT NULL DEFAULT '[]'::jsonb, "
                "ADD COLUMN IF NOT EXISTS compliance_review jsonb NOT NULL DEFAULT '{}'::jsonb, "
                "ADD COLUMN IF NOT EXISTS model text"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS assessments_repo_idx "
                "ON public.assessments (repo)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS assessments_created_at_idx "
                "ON public.assessments (created_at DESC)"
            )
        _PG_SCHEMA_READY = True

def _pg_save(rec):
    import db
    from psycopg.types.json import Jsonb

    _pg_ensure_schema()
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO assessments "
            "(repo, summary, purpose, tech_stack, key_components, vulnerabilities, compliance_review, model) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id, created_at",
            (
                rec["repo"], rec.get("summary"), rec.get("purpose"),
                Jsonb(rec["tech_stack"]), Jsonb(rec["key_components"]),
                Jsonb(rec["vulnerabilities"]), Jsonb(rec["compliance_review"]), rec.get("model"),
            ),
        )
        assessment_id, created_at = cur.fetchone()
    out = dict(rec)
    out["id"] = assessment_id
    out["created_at"] = created_at.isoformat() if hasattr(created_at, "isoformat") else created_at
    return out


def _pg_list(repo, limit):
    import db

    _pg_ensure_schema()
    where, params = [], []
    if repo:
        where.append("repo = %s")
        params.append(repo)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    params.append(limit)

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(_PG_COLS)} FROM assessments {clause} "
            "ORDER BY created_at DESC LIMIT %s",
            params,
        )
        rows = cur.fetchall()

    out = []
    for row in rows:
        d = dict(zip(_PG_COLS, row))
        d["compliance_review"] = d.get("compliance_review") or {}
        if hasattr(d.get("created_at"), "isoformat"):
            d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out
