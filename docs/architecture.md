# Architecture

ReviewBot is a single FastAPI process that serves a React single-page app **and**
a JSON REST API on port 1500. Code reviews are produced by an LLM (via
OpenRouter) grounded in a searchable store of past decisions.

```
                         ┌──────────────────────────────────────────┐
   Browser ──────────────▶  FastAPI (backend/main.py)               │
   (React SPA, :1500)    │   ├── /api/*   REST endpoints            │
                         │   ├── /webhook/github   PR auto-review   │
                         │   └── /*       serves frontend/dist      │
                         └───────┬───────────────┬──────────────────┘
                                 │               │
            ┌────────────────────▼──┐     ┌──────▼─────────────────┐
            │ review_engine          │     │ decision_store          │
            │ (OpenRouter, forced    │◀────│ (Chroma / pgvector,     │
            │  tool call)            │ ctx │  semantic retrieval)    │
            └───────────┬────────────┘     └─────────────────────────┘
                        │
                ┌───────▼────────┐   ┌──────────────┐   ┌──────────────┐
                │ OpenRouter API │   │ config_store │   │ github_backfill│
                └────────────────┘   │ (config.json)│   │ (GitHub API)  │
                                     └──────────────┘   └──────────────┘
```

## Components

| Module | Responsibility |
|---|---|
| `backend/main.py` | FastAPI app: REST endpoints, GitHub webhook, static-file serving. Holds lazily-built singletons for the store and engine. |
| `core/review_engine.py` | Builds the review prompt, retrieves repo-scoped + global context, calls OpenRouter with a **forced `submit_review` tool call**, and maps the structured output to a `ReviewResult`. |
| `core/decision_store.py` | Vector storage abstraction. `ChromaDecisionStore` (local, default) and `PgVectorDecisionStore` (Postgres + pgvector). Both implement `upsert` / `retrieve` / `delete`. |
| `core/config_store.py` | Server-side settings (GitHub token, webhook secret, repo list, models). Two backends: a `config.json` file (local) or a single-row `app_settings` JSONB table (Postgres/Supabase), per `CONFIG_STORE_BACKEND`. Secrets are never returned to clients. |
| `core/embeddings.py` | Text embeddings via an OpenAI-compatible API (default OpenRouter), used by the pgvector store. No local model. |
| `core/db.py` | Per-operation Postgres connection helper (Supabase transaction pooler on serverless). |
| `backend/auth.py` | Optional Supabase Auth gate (`require_user`). No-op unless `SUPABASE_JWT_SECRET` is set. |
| `core/github_backfill.py` | Imports a repo's closed PRs from the GitHub API into the decision store. Shared by the CLI and the API. |
| `cli.py` | `python cli.py backfill <owner/repo> [pages]` — command-line seeding. |
| `frontend/` | React + Vite SPA. Built to `frontend/dist/` and served by FastAPI in production; proxies `/api` to `:1500` in dev. |

## The decision model

A **decision** is one record of how a past PR or ADR was resolved:

| Field | Meaning |
|---|---|
| `doc_id` | Stable unique id (e.g. `org-repo-pr-142`, `manual-…`). |
| `ref` | Human reference (`PR #142`, `ADR-007`). |
| `summary` | What was decided. |
| `reasoning` | Why. |
| `outcome` | `approved_and_merged` / `changes_requested` / `closed_without_merge`. |
| `date` | ISO timestamp. |
| `repo` | The owning repository, **or `*` for a global decision**. |
| `score` | Cosine similarity to the query (retrieval only). |

The embedded text for semantic search is `summary` + `reasoning`. The Chroma
collection uses cosine space; distance is mapped to a `0..1` `score`.

### Per-repo + global scoping

`repo` is the scope axis. A decision is either tied to one repository or marked
**global** (`repo == "*"`) to apply everywhere. Retrieval supports:

- `retrieve(query, k)` — all decisions.
- `retrieve(query, k, repo=R)` — exactly repo `R` (use `"*"` for globals only).
- `retrieve(query, k, repo=R, include_global=True)` — repo `R` **plus** globals.

When reviewing a PR for repo `R`, the engine uses the last form, so a review is
grounded in that repo's precedent **and** org-wide policies, but not other repos.

## Request lifecycle — a review

1. The UI (or webhook) POSTs PR metadata + diff to `/api/review`.
2. `main.py` checks `OPENROUTER_API_KEY`, then calls `engine.review(request)`.
3. The engine queries the store with the PR's title/description/files, scoped to
   `repo + global`, to get the most relevant past decisions.
4. It builds a prompt embedding those decisions and the diff, and calls
   OpenRouter with `tool_choice` forcing the `submit_review` function — so the
   model must return a schema-valid JSON payload.
5. The payload is parsed into a `ReviewResult` (summary, approved, confidence,
   issues, suggestions, applied past decisions) and returned as JSON.

Model and provider are resolved from `config_store` **per review**, so changing
them in the UI takes effect on the next request without a restart.

## GitHub webhook flow

`POST /webhook/github` verifies the `X-Hub-Signature-256` HMAC against the
configured webhook secret, ignores non-`pull_request` events, fetches the PR
diff from the GitHub API, and runs a review. See [api.md](api.md#post-webhookgithub).

## Storage backends

- **Chroma** (default) — local, persistent, zero external services. Data lives in
  `CHROMA_PERSIST_DIR` (default `.chroma`). Embeds with a local ONNX model.
- **pgvector** — Postgres (Supabase) with the `vector` extension. Select with
  `DECISION_STORE_BACKEND=pgvector` + `DATABASE_URL`. Embeds via OpenRouter
  (`core/embeddings.py`), so there's no local model — which is what makes it fit a
  serverless function.

## Deployment topology (Vercel + Supabase)

The same app runs two ways, switched purely by env vars:

| | Local | Vercel + Supabase |
|---|---|---|
| Process | one FastAPI server (`./start.sh`) serving API + SPA | SPA on Vercel CDN; API as a Python serverless function (`api/index.py` → `backend.main:app`) |
| Decisions | ChromaDB (`.chroma`) | Supabase pgvector (`decisions` table) |
| Settings | `config.json` file | Supabase `app_settings` JSONB row |
| Embeddings | local ONNX | OpenRouter API |
| Auth | none | Supabase Auth (JWT-gated `/api/*`) |

`vercel.json` builds `frontend/dist`, routes `/api/*` and `/webhook/*` to the
function, and serves the SPA for everything else. The function bundle
(`api/requirements.txt`) excludes chromadb/onnxruntime to stay under Vercel's size
limit. See [deployment.md](deployment.md).
