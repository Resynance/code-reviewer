"""
compliance_analysis_store.py — persist compliance analysis dashboard results.

Two backends (postgres / file), selected by REVIEW_STORE_BACKEND (or
CONFIG_STORE_BACKEND), mirroring the pattern in review_store.py and
assessment_store.py.
"""

from __future__ import annotations

import os
import json
import threading
from pathlib import Path
from datetime import datetime, timezone

_FILE_PATH = Path(__file__).parent.parent / "compliance_analysis.json"
_LOCK = threading.Lock()
_PG_SCHEMA_LOCK = threading.Lock()
_PG_SCHEMA_READY = False

_FIELDS = ("repo", "health", "coverage", "suggestions")

_PG_COLS = [
    "id", "repo", "health", "coverage", "suggestions", "created_at",
]


def _backend() -> str:
    return os.getenv("REVIEW_STORE_BACKEND") or os.getenv("CONFIG_STORE_BACKEND", "file")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(record: dict) -> dict:
    rec = {k: record.get(k) for k in _FIELDS}
    rec["health"] = rec.get("health") or {}
    rec["coverage"] = rec.get("coverage") or {}
    rec["suggestions"] = rec.get("suggestions") or []
    return rec


def save_analysis(record: dict) -> dict:
    rec = _normalize(record)
    if _backend() == "postgres":
        return _pg_save(rec)
    return _file_save(rec)


def list_analyses(repo: "str | None" = None, limit: int = 20) -> list:
    if _backend() == "postgres":
        return _pg_list(repo, limit)
    return _file_list(repo, limit)


def get_analysis(analysis_id) -> dict | None:
    if _backend() == "postgres":
        return _pg_get(analysis_id)
    return _file_get(analysis_id)


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
        rec["health"] = rec.get("health") or {}
        rec["coverage"] = rec.get("coverage") or {}
        rec["suggestions"] = rec.get("suggestions") or []
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


def _file_get(analysis_id):
    data = _file_read()
    for row in data:
        if row.get("id") == analysis_id:
            return row
    return None


# ----- postgres backend ----- #

def _pg_ensure_schema():
    """Create/upgrade the compliance_analysis table in-place for older deployments."""
    global _PG_SCHEMA_READY
    if _PG_SCHEMA_READY:
        return
    import db

    with _PG_SCHEMA_LOCK:
        if _PG_SCHEMA_READY:
            return
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS public.compliance_analysis ("
                "  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,"
                "  repo text NOT NULL,"
                "  health jsonb NOT NULL DEFAULT '{}'::jsonb,"
                "  coverage jsonb NOT NULL DEFAULT '{}'::jsonb,"
                "  suggestions jsonb NOT NULL DEFAULT '[]'::jsonb,"
                "  created_at timestamptz NOT NULL DEFAULT now()"
                ")"
            )
            cur.execute(
                "ALTER TABLE public.compliance_analysis "
                "ADD COLUMN IF NOT EXISTS health jsonb NOT NULL DEFAULT '{}'::jsonb, "
                "ADD COLUMN IF NOT EXISTS coverage jsonb NOT NULL DEFAULT '{}'::jsonb, "
                "ADD COLUMN IF NOT EXISTS suggestions jsonb NOT NULL DEFAULT '[]'::jsonb"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS compliance_analysis_repo_idx "
                "ON public.compliance_analysis (repo)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS compliance_analysis_created_at_idx "
                "ON public.compliance_analysis (created_at DESC)"
            )
        _PG_SCHEMA_READY = True

def _pg_save(rec):
    import db
    from psycopg.types.json import Jsonb

    _pg_ensure_schema()
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO compliance_analysis "
            "(repo, health, coverage, suggestions) "
            "VALUES (%s,%s,%s,%s) RETURNING id, created_at",
            (
                rec["repo"], Jsonb(rec["health"]), Jsonb(rec["coverage"]), Jsonb(rec["suggestions"]),
            ),
        )
        analysis_id, created_at = cur.fetchone()
    out = dict(rec)
    out["id"] = analysis_id
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
            f"SELECT {', '.join(_PG_COLS)} FROM compliance_analysis {clause} "
            "ORDER BY created_at DESC LIMIT %s",
            params,
        )
        rows = cur.fetchall()

    out = []
    for row in rows:
        d = dict(zip(_PG_COLS, row))
        d["health"] = d.get("health") or {}
        d["coverage"] = d.get("coverage") or {}
        d["suggestions"] = d.get("suggestions") or []
        if hasattr(d.get("created_at"), "isoformat"):
            d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out


def _pg_get(analysis_id):
    import db

    _pg_ensure_schema()
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(_PG_COLS)} FROM compliance_analysis WHERE id = %s",
            (analysis_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    d = dict(zip(_PG_COLS, row))
    d["health"] = d.get("health") or {}
    d["coverage"] = d.get("coverage") or {}
    d["suggestions"] = d.get("suggestions") or []
    if hasattr(d.get("created_at"), "isoformat"):
        d["created_at"] = d["created_at"].isoformat()
    return d
