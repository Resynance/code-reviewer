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

_FIELDS = (
    "repo", "summary", "purpose",
    "tech_stack", "key_components", "vulnerabilities", "model",
)

_PG_COLS = [
    "id", "repo", "summary", "purpose",
    "tech_stack", "key_components", "vulnerabilities", "model", "created_at",
]


def _backend() -> str:
    return os.getenv("REVIEW_STORE_BACKEND") or os.getenv("CONFIG_STORE_BACKEND", "file")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(record: dict) -> dict:
    rec = {k: record.get(k) for k in _FIELDS}
    for k in ("tech_stack", "key_components", "vulnerabilities"):
        rec[k] = rec.get(k) or []
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
            return json.load(f)
    except (ValueError, OSError):
        return []


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

def _pg_save(rec):
    import db

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO assessments "
            "(repo, summary, purpose, tech_stack, key_components, vulnerabilities, model) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id, created_at",
            (
                rec["repo"], rec.get("summary"), rec.get("purpose"),
                json.dumps(rec["tech_stack"]), json.dumps(rec["key_components"]),
                json.dumps(rec["vulnerabilities"]), rec.get("model"),
            ),
        )
        assessment_id, created_at = cur.fetchone()
    out = dict(rec)
    out["id"] = assessment_id
    out["created_at"] = created_at.isoformat() if hasattr(created_at, "isoformat") else created_at
    return out


def _pg_list(repo, limit):
    import db

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
        if hasattr(d.get("created_at"), "isoformat"):
            d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out
