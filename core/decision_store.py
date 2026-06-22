"""
decision_store.py — vector storage for past review decisions.

A "decision" is one record of how a past PR / ADR was resolved: a short
summary, the reasoning behind it, and the eventual outcome. The reviewer
retrieves semantically-similar decisions to ground new reviews in precedent.

Two backends are available, selected via the DECISION_STORE_BACKEND env var:
  - "chroma"   (default) — local, persistent, no external services
  - "pgvector"           — Postgres + pgvector for production deployments

Both implement the same interface:
  upsert(doc_id, ref, summary, reasoning, outcome, date, metadata)
  retrieve(query, k)      -> list[dict]
  delete(doc_id)
"""

import os
import json
from typing import Optional

import db
import embeddings


# Fields we promote to first-class columns / metadata. Anything else the caller
# passes in `metadata` is JSON-encoded into a single side field.
_CORE_FIELDS = ("ref", "summary", "reasoning", "outcome", "date")

# Sentinel repo value marking a decision as global (applies to every repo).
GLOBAL_REPO = "*"


def _chroma_where(repo, include_global):
    """Build a Chroma `where` filter for a repo scope.

    - repo falsy            -> None (no filter; matches everything)
    - include_global + repo -> match the repo OR global decisions
    - repo only             -> exact match (use GLOBAL_REPO to get globals only)
    """
    if not repo:
        return None
    if include_global and repo != GLOBAL_REPO:
        return {"repo": {"$in": [repo, GLOBAL_REPO]}}
    return {"repo": repo}


def _flatten_metadata(ref, summary, reasoning, outcome, date, metadata):
    """Build a flat, scalar-only metadata dict (Chroma rejects nested values)."""
    flat = {
        "ref": ref or "",
        "summary": summary or "",
        "reasoning": reasoning or "",
        "outcome": outcome or "",
        "date": date or "",
    }
    if metadata:
        # Keep scalar extras inline; stash the rest as JSON so retrieve() can
        # reconstruct it without losing information.
        extra = {}
        for key, value in metadata.items():
            if key in flat:
                continue
            if isinstance(value, (str, int, float, bool)):
                flat[key] = value
            else:
                extra[key] = value
        if extra:
            flat["_extra_json"] = json.dumps(extra, sort_keys=True)
    return flat


def _record_from_metadata(doc_id, meta, score=None):
    """Turn a stored metadata dict back into the public decision shape."""
    meta = dict(meta or {})
    extra_json = meta.pop("_extra_json", None)
    record = {
        "doc_id": doc_id,
        "ref": meta.get("ref", ""),
        "summary": meta.get("summary", ""),
        "reasoning": meta.get("reasoning", ""),
        "outcome": meta.get("outcome", ""),
        "date": meta.get("date", ""),
    }
    # Surface any remaining scalar metadata fields too.
    for key, value in meta.items():
        if key not in record:
            record[key] = value
    if extra_json:
        try:
            record["metadata"] = json.loads(extra_json)
        except (ValueError, TypeError):
            pass
    if score is not None:
        record["score"] = score
    return record


class ChromaDecisionStore:
    """Local, persistent decision store backed by ChromaDB."""

    def __init__(self, persist_dir: Optional[str] = None, collection: str = "decisions"):
        import chromadb
        from chromadb.config import Settings

        self.persist_dir = persist_dir or os.getenv("CHROMA_PERSIST_DIR", ".chroma")
        # Disable Chroma's anonymous telemetry — it's noisy and its opentelemetry
        # path can crash on some environments during upsert/query.
        self._client = chromadb.PersistentClient(
            path=self.persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        # Cosine space keeps distances in [0, 2]; we map that to a 0..1 score.
        self._collection = self._client.get_or_create_collection(
            name=collection,
            metadata={"hnsw:space": "cosine"},
        )

    def upsert(self, doc_id, ref, summary, reasoning, outcome, date, metadata=None):
        # The embedded document is what we search against — summary plus reasoning
        # captures both the decision and the rationale.
        document = "\n\n".join(p for p in (summary, reasoning) if p)
        self._collection.upsert(
            ids=[doc_id],
            documents=[document or summary or ref or doc_id],
            metadatas=[_flatten_metadata(ref, summary, reasoning, outcome, date, metadata)],
        )

    def retrieve(self, query, k=10, repo=None, include_global=False):
        if self._collection.count() == 0:
            return []
        n = max(1, min(k, self._collection.count()))
        where = _chroma_where(repo, include_global)
        result = self._collection.query(query_texts=[query], n_results=n, where=where)

        ids = (result.get("ids") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        dists = (result.get("distances") or [[]])[0]

        records = []
        for i, doc_id in enumerate(ids):
            meta = metas[i] if i < len(metas) else {}
            score = None
            if i < len(dists) and dists[i] is not None:
                # Cosine distance → similarity, clamped to [0, 1].
                score = max(0.0, min(1.0, 1.0 - float(dists[i])))
            records.append(_record_from_metadata(doc_id, meta, score))
        return records

    def delete(self, doc_id):
        self._collection.delete(ids=[doc_id])

    def existing_ids(self, doc_ids):
        """Return the subset of `doc_ids` already present in the store."""
        ids = [d for d in doc_ids if d]
        if not ids:
            return set()
        result = self._collection.get(ids=ids)
        return set(result.get("ids") or [])

    def close(self):
        """Release Chroma resources so tests do not leak file descriptors."""
        if getattr(self, "_client", None) is not None:
            self._client.close()


class PgVectorDecisionStore:
    """Postgres + pgvector backend for production / serverless deployments.

    Embeddings come from `core/embeddings.py` (an OpenAI-compatible API, default
    OpenRouter) — no local model. Connections are opened per-operation via
    `core/db.py` (use the Supabase transaction pooler on serverless). Requires
    `psycopg`, `pgvector`, and DATABASE_URL. The schema is normally created by the
    Supabase migration; `EMBEDDING_DIM` must match the `vector(N)` column.
    """

    def __init__(self, table: str = "decisions"):
        if not os.getenv("DATABASE_URL"):
            raise RuntimeError("DATABASE_URL must be set for the pgvector backend.")
        self._table = table
        self._dim = embeddings.get_dim()
        # Schema is normally applied once via the Supabase migration. Locally we
        # create it on demand for convenience; on Vercel we never run DDL.
        if os.getenv("PGVECTOR_ENSURE_SCHEMA", "1") == "1" and not os.getenv("VERCEL"):
            try:
                self.ensure_schema()
            except Exception:
                pass

    def ensure_schema(self):
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {self._table} ("
                "doc_id TEXT PRIMARY KEY, metadata JSONB NOT NULL, "
                f"embedding vector({self._dim}) NOT NULL)"
            )

    def upsert(self, doc_id, ref, summary, reasoning, outcome, date, metadata=None):
        document = "\n\n".join(p for p in (summary, reasoning) if p) or ref or doc_id
        embedding = embeddings.embed([document])[0]
        meta = _flatten_metadata(ref, summary, reasoning, outcome, date, metadata)
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {self._table} (doc_id, metadata, embedding) VALUES (%s, %s, %s) "
                "ON CONFLICT (doc_id) DO UPDATE SET "
                "metadata = EXCLUDED.metadata, embedding = EXCLUDED.embedding",
                (doc_id, json.dumps(meta), str(list(embedding))),
            )

    def retrieve(self, query, k=10, repo=None, include_global=False):
        embedding = embeddings.embed([query])[0]
        emb = str(list(embedding))
        where = ""
        params = [emb]
        if repo:
            if include_global and repo != GLOBAL_REPO:
                where = "WHERE metadata->>'repo' IN (%s, %s)"
                params.extend([repo, GLOBAL_REPO])
            else:
                where = "WHERE metadata->>'repo' = %s"
                params.append(repo)
        params.extend([emb, k])
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT doc_id, metadata, 1 - (embedding <=> %s) AS score "
                f"FROM {self._table} {where} ORDER BY embedding <=> %s LIMIT %s",
                params,
            )
            rows = cur.fetchall()
        return [
            _record_from_metadata(doc_id, meta, max(0.0, min(1.0, float(score))))
            for doc_id, meta, score in rows
        ]

    def delete(self, doc_id):
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute(f"DELETE FROM {self._table} WHERE doc_id = %s", (doc_id,))

    def existing_ids(self, doc_ids):
        """Return the subset of `doc_ids` already present in the store."""
        ids = [d for d in doc_ids if d]
        if not ids:
            return set()
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT doc_id FROM {self._table} WHERE doc_id = ANY(%s)", (ids,)
            )
            return {row[0] for row in cur.fetchall()}


def create_store():
    """Factory: build the decision store named by DECISION_STORE_BACKEND."""
    backend = os.getenv("DECISION_STORE_BACKEND", "chroma").lower()
    if backend == "pgvector":
        return PgVectorDecisionStore()
    if backend == "chroma":
        return ChromaDecisionStore()
    raise ValueError(
        f"Unknown DECISION_STORE_BACKEND {backend!r}. Use 'chroma' or 'pgvector'."
    )
