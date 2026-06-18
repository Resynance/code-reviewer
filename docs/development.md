# Development Guide

## Prerequisites

- **Python 3.10+** (3.9 works at runtime, but `setup.sh` checks for 3.10+).
- **Node.js 18+** for the frontend build.

## Setup

Run the installer **as your normal user — not with `sudo`**:

```bash
./setup.sh
```

> ⚠️ Running `sudo ./setup.sh` creates a root-owned `.venv/` and
> `frontend/dist/`, which then break `pip install` and `npm run build` for your
> user. If you hit permission errors, reset with
> `sudo rm -rf .venv frontend/dist && ./setup.sh` (no sudo).

`setup.sh` creates `.venv/`, installs the Python deps + `pytest`, runs
`npm install` + `npm run build`, and writes a `.env` template.

## Running

**Production-style (single process serves API + built frontend):**
```bash
./start.sh          # uvicorn on :1500 with --reload
```

**Frontend dev server (hot reload, proxies /api → :1500):**
```bash
# terminal 1 — backend
source .venv/bin/activate && uvicorn backend.main:app --reload --port 1500
# terminal 2 — frontend
cd frontend && npm run dev      # Vite on :5173
```

## Project layout

```
backend/main.py            FastAPI app — endpoints, webhook, static serving
core/
  review_engine.py         LLM orchestration (OpenRouter, forced tool call)
  decision_store.py        Chroma / pgvector vector store
  config_store.py          config.json-backed settings
  github_backfill.py       GitHub PR import (shared by CLI + API)
cli.py                     `python cli.py backfill <owner/repo> [pages]`
frontend/src/
  App.jsx, main.jsx        SPA entry
  lib/api.js               REST client
  components/Sidebar.jsx    nav + balance badge
  pages/{Review,Decisions,Settings}Page.jsx
tests/                     pytest suite
docs/                      this documentation
```

## Tests

```bash
source .venv/bin/activate
python -m pytest
```

The suite (`tests/`) covers config store, decision store + repo/global scoping,
backfill error handling, the review engine, and every API endpoint. Design:

- **Real ChromaDB** in a temp dir for store/API tests (true integration).
- **All network mocked** — the OpenRouter client and GitHub HTTP calls are faked,
  so tests are deterministic and offline.
- **Isolated** — temp `config.json` and `CHROMA_PERSIST_DIR` via fixtures
  (`conftest.py`); your real `config.json`/`.chroma` are never touched.

Shared fixtures live in `conftest.py`: `cfg` (temp config store), `store` (temp
Chroma store), and `client` (FastAPI `TestClient` + temp backing).

## Extending

**Add a decision-store backend.** Implement a class with
`upsert(doc_id, ref, summary, reasoning, outcome, date, metadata)`,
`retrieve(query, k, repo=None, include_global=False)`, and `delete(doc_id)`, then
wire it into `create_store()` in `core/decision_store.py`. Honor the `repo == "*"`
global sentinel in `retrieve`.

**Change the review output schema.** Edit `_REVIEW_SCHEMA` in
`core/review_engine.py` (the JSON schema the model must satisfy) and update
`_to_result` to map any new fields onto `ReviewResult`. Keep the frontend
(`ReviewPage.jsx`) in sync.

**Add an endpoint.** Add it to `backend/main.py` above the frontend catch-all
route, add a method to `frontend/src/lib/api.js`, and add a test in
`tests/test_api.py`.

## Configuration & secrets

Settings set in the UI persist to `config.json` (gitignored); env vars in `.env`
are fallbacks. See the [README](../README.md#configuration) for the full table.
The GitHub token and webhook secret are stored in plaintext in `config.json` —
acceptable for this local, single-user tool. They are **never** returned by the
API (`GET /api/settings` reports booleans only).
