"""
db.py — Postgres connection helper for the Supabase/serverless path.

Opens a short-lived connection per operation from DATABASE_URL. On Vercel this
should be the Supabase **transaction pooler** URL (port 6543), which is built for
many brief serverless connections. Used by the pgvector decision store and the
Postgres config store.
"""

import os


def connect():
    """Open a new autocommit Postgres connection. Caller closes it (use `with`)."""
    import psycopg

    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg.connect(url, autocommit=True)
