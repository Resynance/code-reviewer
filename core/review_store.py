"""
review_store.py — persist the result of every review run (full history).

Two backends, selected by REVIEW_STORE_BACKEND (default follows
CONFIG_STORE_BACKEND, so it's Postgres on Vercel and a local JSON file otherwise):
  - "postgres": appends to the public.reviews table via core/db.py.
  - "file":     appends to reviews.json at the project root (gitignored).

Records are append-only — re-reviewing a PR adds a new row, preserving history.
"""

import os
import json
import threading
from pathlib import Path
from datetime import datetime, timezone

_FILE_PATH = Path(__file__).parent.parent / "reviews.json"
_LOCK = threading.Lock()

# Fields accepted on a review record (besides id/created_at, which are assigned).
_FIELDS = (
    "repo", "pr_number", "title", "author", "approved", "confidence",
    "summary", "issues", "suggestions", "past_decisions", "compliance_review", "source", "model",
)


def _backend() -> str:
    return os.getenv("REVIEW_STORE_BACKEND") or os.getenv("CONFIG_STORE_BACKEND", "file")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(record: dict) -> dict:
    rec = {k: record.get(k) for k in _FIELDS}
    for k in ("issues", "suggestions", "past_decisions"):
        rec[k] = rec.get(k) or []
    rec["compliance_review"] = rec.get("compliance_review") or {}
    return rec


def save_review(record: dict) -> dict:
    rec = _normalize(record)
    if _backend() == "postgres":
        return _pg_save(rec)
    return _file_save(rec)


def list_reviews(repo=None, pr_number=None, limit=50) -> list:
    if _backend() == "postgres":
        return _pg_list(repo, pr_number, limit)
    return _file_list(repo, pr_number, limit)


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


def _file_list(repo, pr_number, limit):
    data = _file_read()
    if repo:
        data = [r for r in data if r.get("repo") == repo]
    if pr_number is not None:
        data = [r for r in data if r.get("pr_number") == pr_number]
    return list(reversed(data))[:limit]  # newest first


# ----- postgres backend ----- #

_PG_COLS = [
    "id", "repo", "pr_number", "title", "author", "approved", "confidence",
    "summary", "issues", "suggestions", "past_decisions", "compliance_review", "source", "model", "created_at",
]


def _pg_save(rec):
    import db

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO reviews "
            "(repo, pr_number, title, author, approved, confidence, summary, "
            " issues, suggestions, past_decisions, compliance_review, source, model) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id, created_at",
            (
                rec["repo"], rec["pr_number"], rec.get("title"), rec.get("author"),
                rec.get("approved"), rec.get("confidence"), rec.get("summary"),
                json.dumps(rec["issues"]), json.dumps(rec["suggestions"]),
                json.dumps(rec["past_decisions"]), json.dumps(rec["compliance_review"]),
                rec.get("source"), rec.get("model"),
            ),
        )
        review_id, created_at = cur.fetchone()
    out = dict(rec)
    out["id"] = review_id
    out["created_at"] = created_at.isoformat() if hasattr(created_at, "isoformat") else created_at
    return out


def _pg_list(repo, pr_number, limit):
    import db

    where, params = [], []
    if repo:
        where.append("repo = %s")
        params.append(repo)
    if pr_number is not None:
        where.append("pr_number = %s")
        params.append(pr_number)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    params.append(limit)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(_PG_COLS)} FROM reviews {clause} "
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
