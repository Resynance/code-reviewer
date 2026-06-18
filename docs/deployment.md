# Deploying on Vercel + Supabase

ReviewBot runs on Vercel as a static SPA (CDN) plus a Python serverless function
(the FastAPI API), backed by **Supabase Postgres** (pgvector for decisions, a
JSONB row for settings), with embeddings via **OpenRouter** and access gated by
**Supabase Auth**.

> Local dev is unaffected — without the Supabase/Postgres env vars the app runs
> exactly as before (ChromaDB + `config.json` + no auth). See
> [development.md](development.md).

## 1. Supabase

1. Create a Supabase project.
2. **Enable pgvector + create the schema**: open the SQL editor and run
   [`supabase/migrations/0001_init.sql`](../supabase/migrations/0001_init.sql).
   It creates the `decisions` table (`vector(1536)` + HNSW cosine index), the
   `app_settings` JSONB row, and enables RLS (deny-all — these tables are read
   directly by the app, not via the Data API).
3. **Connection string** (`DATABASE_URL`): Project Settings → Database →
   Connection string → **Transaction pooler** (Supavisor, port **6543**). This is
   the serverless-safe pooled connection. Example:
   `postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres`
4. **Auth**:
   - Authentication → Providers → Email (enabled).
   - Authentication → Sign In / Providers → **disable open sign-ups** (so only
     people you invite can access the app). Create your user(s) under
     Authentication → Users → *Add user* (set "Auto Confirm User").
   - Grab the **Project URL** and the **anon / publishable key** (Project
     Settings → API). Backend token verification uses the project's JWKS
     (derived from `SUPABASE_URL`) — no JWT secret needed for asymmetric keys.

## 2. Vercel

Import the GitHub repo into Vercel (Root Directory = repo root; `vercel.json`
configures the build, the Python function, and routing). Then set env vars under
**Project Settings → Environment Variables**:

### Function (server) env

| Var | Value |
|---|---|
| `DATABASE_URL` | Supabase **transaction pooler** URL (port 6543) — mark Sensitive |
| `DECISION_STORE_BACKEND` | `pgvector` |
| `CONFIG_STORE_BACKEND` | `postgres` |
| `OPENROUTER_API_KEY` | your OpenRouter key — Sensitive |
| `EMBEDDING_MODEL` | e.g. `openai/text-embedding-3-small` (must be served by your embeddings endpoint) |
| `EMBEDDING_DIM` | `1536` — **must match** the `vector(N)` in the migration |
| `SUPABASE_URL` | `https://<ref>.supabase.co` — used to verify login tokens via the project JWKS (current projects sign tokens with asymmetric ES256/RS256 keys) |
| `SUPABASE_JWT_SECRET` | *(alternative)* only if your project still uses the legacy HS256 secret instead of asymmetric signing keys — Sensitive |
| `ALLOWED_EMAILS` | *(optional)* comma-separated allowlist, e.g. `you@co.com` |
| `OPENROUTER_MODEL` / `OPENROUTER_PROVIDER` | *(optional)* defaults apply |
| `EMBEDDINGS_BASE_URL` / `EMBEDDINGS_API_KEY` | *(optional)* override if OpenRouter doesn't serve your embeddings model (e.g. point at OpenAI) |
| `OPENROUTER_APP_URL` | *(optional)* your prod URL, for OpenRouter attribution |

`GITHUB_TOKEN` / `GITHUB_WEBHOOK_SECRET` can be set here as fallbacks, but it's
easier to set them in the app's **Settings** page once deployed (persisted to
`app_settings`).

### Build (frontend) env

| Var | Value |
|---|---|
| `VITE_SUPABASE_URL` | Supabase Project URL |
| `VITE_SUPABASE_ANON_KEY` | Supabase anon (publishable) key |

These are read at build time; the frontend enables the login screen only when
both are present.

Deploy. Vercel builds the SPA (`frontend/dist`) and the function (`api/index.py`,
deps from `api/requirements.txt` — no chromadb/onnxruntime).

## 3. After deploy

1. Open the Vercel URL → log in with the Supabase user you created.
2. **Settings** → set the GitHub token (and webhook secret), pick the review +
   embedding models, add repositories.
3. **Backfill** a repo, load a PR via the picker, run a review.
4. **Webhook** (optional auto-review): in the GitHub repo → Settings → Webhooks,
   add `https://<your-vercel-domain>/webhook/github`, content type
   `application/json`, secret = the webhook secret you set, events = *Pull
   requests*.

## Notes / limits

- **Embedding model ↔ dimension**: `EMBEDDING_MODEL` must be served by your
  embeddings endpoint, and its dimension must equal `EMBEDDING_DIM` and the
  migration's `vector(N)`. ⚠️ Verify OpenRouter serves your chosen embeddings
  model; if not, set `EMBEDDINGS_BASE_URL`/`EMBEDDINGS_API_KEY` to a provider
  that does (e.g. OpenAI) — the same model/dim still applies.
- **Function timeout**: `vercel.json` sets `maxDuration: 60` (safe on all plans).
  On Pro you can raise it (up to 300) if a large review ever approaches the limit.
- **Backfill size**: large backfills may exceed the function timeout. Keep page
  counts modest from the UI, or run the CLI locally against the Supabase
  `DATABASE_URL`: `python cli.py backfill org/repo 20`.
- **Fresh store**: Supabase starts empty; existing local Chroma decisions don't
  carry over (different embedder/dimension) — re-backfill into Supabase.
