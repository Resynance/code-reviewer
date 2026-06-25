# ReviewBot — AI Code Review Tool

AI-powered pull-request reviews grounded in your team's past decisions. A diff
goes in; a structured review comes out — issues, suggestions, and an
approve/changes verdict — informed by semantically-similar prior PRs and ADRs.

**Features**
- LLM reviews via [OpenRouter](https://openrouter.ai) — any model, configurable in the UI.
- Optional **local LLM queue** mode — persist requests in the DB for a separate worker running on your machine to claim and complete.
- A searchable **decision store** (ChromaDB or pgvector) seeded by backfilling
  closed GitHub PRs.
- **Per-repo + global** decision scoping — a review sees its repo's precedent plus org-wide policies.
- GitHub **webhook** for automatic reviews on new PRs.
- All settings (model, provider, GitHub creds, repos) managed from the web UI.
- Runs locally (ChromaDB, no auth) **or** on **Vercel + Supabase** (pgvector,
  Supabase Auth) — same codebase, switched by env vars. See
  [docs/deployment.md](docs/deployment.md).

## Documentation

| Doc | What's in it |
|---|---|
| [docs/architecture.md](docs/architecture.md) | System design, components, the decision model, request lifecycle |
| [docs/api.md](docs/api.md) | Full REST API reference (also auto-served at `/docs` and `/redoc`) |
| [docs/development.md](docs/development.md) | Dev setup, project layout, tests, how to extend |
| [docs/deployment.md](docs/deployment.md) | Deploying on Vercel + Supabase (pgvector, Auth, env vars) |

## Quick Start

```bash
# 1. Install everything (run once)
./setup.sh

# 2. Add your API key
nano .env   # set OPENROUTER_API_KEY (and optionally OPENROUTER_MODEL)

# 3. Start the server
./start.sh
# → http://localhost:1500
```

## Project Structure

```
code-reviewer/
├── setup.sh / start.sh     ← install (once) / run on :1500
├── .env                    ← created by setup.sh, fallback config
├── config.json             ← UI-managed settings (gitignored)
├── cli.py                  ← backfill command
├── backend/main.py         ← FastAPI app (API + webhook + serves frontend)
├── core/
│   ├── review_engine.py    ← LLM orchestration (OpenRouter, forced tool call)
│   ├── decision_store.py   ← vector DB (Chroma or pgvector)
│   ├── config_store.py     ← config.json-backed settings
│   └── github_backfill.py  ← GitHub PR import
├── frontend/               ← React source (Vite) + built dist/
├── tests/                  ← pytest suite
└── docs/                   ← architecture, API, development guides
```

See [docs/development.md](docs/development.md#project-layout) for the full layout.

## What each script does

**setup.sh**
- Creates a Python virtualenv at `.venv/`
- Rebuilds `.venv/` automatically if the embedded `pip` metadata is corrupted
- Installs FastAPI, uvicorn, ChromaDB, OpenAI SDK (for OpenRouter), etc.
- Runs `npm install` + `npm run build` for the React frontend
- Creates a `.env` template if one doesn't exist

**start.sh**
- Activates `.venv`
- Loads `.env`
- Rebuilds the frontend if source files changed since last build
- Starts uvicorn on port 1500 with `--reload` watching `backend/` and `core/`

## Configuration

Settings are managed from the **Settings page** in the UI and persisted
server-side to `config.json` (gitignored):

- **Model & provider** — the OpenRouter model slug, and an optional upstream
  provider to pin (e.g. `Anthropic`). Changes take effect on the next review,
  no restart needed.
- **LLM execution mode** — run inside this app (`inline`) or queue jobs for a
  local worker (`local_queue`) using a shared worker secret.
- **Local review agents** — when using `local_queue`, configure one or more
  local agent commands (for example Codex and Kimi) that agentic reviews
  can fan out to from the local worker.
- **Queue viewer** — inspect queued/running/done/error local-execution jobs,
  including claim state and worker errors, from the Queue page.
- **GitHub token** and **webhook secret** — entered in the GitHub Access form.
- **Repositories** — added/removed in the Repositories card; each has a
  **Backfill** button that imports its closed PRs into the decision store.

Environment variables (below) act as fallback defaults when the corresponding
value isn't set in `config.json`.

### Decision scoping (per-repo + global)

Every decision is scoped to a repository **or** marked **global** (applies to
all repos). When reviewing a PR, the engine grounds the review in that repo's
decisions **plus** all global decisions. The Decisions page filters by
**All**, **Global**, or a specific repo, and the Review form picks the repo from
a dropdown of configured repos.

## Environment variables (.env)

| Variable | Required | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | Yes | Get from openrouter.ai/keys |
| `OPENROUTER_BASE_URL` | No | Base URL for inline review/assessment model calls. Default: `https://openrouter.ai/api/v1`. Can point to any OpenAI-compatible server, for example `http://192.168.0.197:8080/`. |
| `OPENROUTER_MODEL` | No | Model slug from openrouter.ai/models (default: `anthropic/claude-sonnet-4.5`) |
| `LLM_EXECUTION_MODE` | No | `inline` (default) or `local_queue` |
| `LLM_WORKER_SECRET` | No | Shared secret for the local worker endpoints |
| `LOCAL_LLM_BASE_URL` | No | Base URL for an OpenAI-compatible local LLM server used by `local_worker.py` for queued local-LLM reviews/assessments. Examples: `http://localhost:8080/` or `http://localhost:11434/v1` |
| `GITHUB_TOKEN` | Fallback | PAT with `repo` read scope (prefer setting it in the UI) |
| `GITHUB_WEBHOOK_SECRET` | Fallback | Any random string (prefer setting it in the UI) |
| `DECISION_STORE_BACKEND` | No | `chroma` (default) or `pgvector` |
| `CHROMA_PERSIST_DIR` | No | Where Chroma stores data (default: `.chroma`) |

## Tests

The backend has a pytest suite covering the config store, decision store and
repo/global scoping, GitHub backfill error handling, the review engine (with the
LLM client faked — no network), and the FastAPI endpoints end-to-end.

```bash
source .venv/bin/activate
python -m pytest
```

The suite uses temporary config and Chroma directories, mocks all network calls
(OpenRouter and GitHub), and does not touch your real `config.json` or `.chroma`.

## Local agentic reviews

When the app is set to `local_queue`, the Review page can optionally submit a
review in **agentic** mode to multiple local sources. The worker reads the
`local_review_agents` list from Settings, runs each enabled command with the PR
prompt, and merges the structured outputs into one review result.

For this agentic-only local path, `LLM_WORKER_SECRET` is optional. It is still
required for queued local-LLM reviews and assessments.

For queued local-LLM reviews and assessments, `local_worker.py` reads
`LOCAL_LLM_BASE_URL` directly. Both root-style endpoints such as
`http://localhost:8080/` and versioned endpoints such as
`http://localhost:11434/v1` are supported.

For inline reviews and assessments, the backend uses `OPENROUTER_BASE_URL`
from the environment or the Settings page. This can also point at a LAN-hosted
OpenAI-compatible server such as `http://192.168.0.197:8080/`.
The Settings page includes a `Test endpoint` button that probes the configured
URL and suggests adding or removing `/v1` for common llama.cpp-style setups.

The default local agent list includes:
- `codex` using `codex exec`
- `kimi` using `kimi -p ... --output-format stream-json`

Command entries can use these placeholders:
- `{schema_path}` — JSON schema file the agent should match
- `{output_path}` — file path the agent can write its final JSON response to
- `{prompt}` — full review prompt passed inline for CLIs that do not read stdin directly

## Seeding the decision store

Use the **Backfill** button next to a repo on the Settings page, or run the CLI:

```bash
source .venv/bin/activate
python cli.py backfill your-org/your-repo 10
```
