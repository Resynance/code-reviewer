"""
access_store.py — the runtime user allowlist (which signed-in users may use the
app). Editable without a redeploy.

Two backends, selected by CONFIG_STORE_BACKEND (Postgres on Vercel, else a local
JSON file). `allowed_emails_cached()` caches the set briefly so the auth check
doesn't hit the DB on every request; mutations invalidate the cache.
"""

import os
import json
import time
import threading
from pathlib import Path

_FILE_PATH = Path(__file__).parent.parent / "access.json"
_LOCK = threading.Lock()
_CACHE = {"emails": None, "ts": 0.0}
_TTL = 30.0  # seconds


def _backend() -> str:
    return os.getenv("CONFIG_STORE_BACKEND", "file")


def _invalidate():
    _CACHE["emails"] = None


def list_emails() -> list:
    return _pg_list() if _backend() == "postgres" else _file_list()


def add_email(email: str) -> list:
    e = (email or "").strip().lower()
    if e:
        (_pg_add if _backend() == "postgres" else _file_add)(e)
        _invalidate()
    return list_emails()


def remove_email(email: str) -> list:
    e = (email or "").strip().lower()
    (_pg_remove if _backend() == "postgres" else _file_remove)(e)
    _invalidate()
    return list_emails()


def allowed_emails_cached() -> set:
    """Cached set of allowed emails (TTL ~30s). Raises if the backend is down."""
    now = time.monotonic()
    if _CACHE["emails"] is None or now - _CACHE["ts"] > _TTL:
        _CACHE["emails"] = set(list_emails())
        _CACHE["ts"] = now
    return _CACHE["emails"]


# ----- file backend ----- #

def _file_read():
    if not _FILE_PATH.exists():
        return []
    try:
        with open(_FILE_PATH) as f:
            return json.load(f)
    except (ValueError, OSError):
        return []


def _file_write(emails):
    tmp = _FILE_PATH.with_name(_FILE_PATH.name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(sorted(set(emails)), f, indent=2)
    tmp.replace(_FILE_PATH)


def _file_list():
    return _file_read()


def _file_add(e):
    with _LOCK:
        emails = set(_file_read())
        emails.add(e)
        _file_write(emails)


def _file_remove(e):
    with _LOCK:
        emails = set(_file_read())
        emails.discard(e)
        _file_write(emails)


# ----- postgres backend ----- #

def _pg_list():
    import db

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT email FROM access_allowlist ORDER BY email")
        return [r[0] for r in cur.fetchall()]


def _pg_add(e):
    import db

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO access_allowlist (email) VALUES (%s) ON CONFLICT (email) DO NOTHING",
            (e,),
        )


def _pg_remove(e):
    import db

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM access_allowlist WHERE email = %s", (e,))
