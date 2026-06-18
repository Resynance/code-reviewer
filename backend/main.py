"""
Code Review Tool — FastAPI backend
Serves the React frontend as static files and exposes REST endpoints.
"""

import os
import sys
import json
import hmac
import hashlib
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

# Add parent dir so we can import the core modules
sys.path.insert(0, str(Path(__file__).parent.parent / "core"))

import config_store
from decision_store import create_store, ChromaDecisionStore
from review_engine import CodeReviewEngine, ReviewRequest
from github_backfill import (
    backfill as run_backfill,
    list_open_prs,
    list_prs,
    fetch_pr,
    pr_doc_id,
)


# ------------------------------------------------------------------ #
# App setup
# ------------------------------------------------------------------ #

app = FastAPI(title="Code Review Tool API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_store = None
_engine = None


def get_store():
    global _store
    if _store is None:
        _store = create_store()
    return _store


def get_engine():
    global _engine
    if _engine is None:
        _engine = CodeReviewEngine(get_store())
    return _engine


# ------------------------------------------------------------------ #
# Request/Response models
# ------------------------------------------------------------------ #

class ReviewRequestBody(BaseModel):
    pr_number: int
    repo: str
    title: str
    description: str = ""
    diff: str
    author: str = "unknown"
    base_branch: str = "main"
    files_changed: list[str] = []


class DecisionUpsertBody(BaseModel):
    ref: str
    summary: str
    reasoning: str
    outcome: str
    metadata: dict = {}


class SearchBody(BaseModel):
    query: str
    k: int = 10
    repo: Optional[str] = None


class SettingsBody(BaseModel):
    # All optional: only provided (non-null) fields are updated. Send "" to clear
    # a value (falls back to env/default); omit/null to leave it unchanged.
    github_token: Optional[str] = None
    webhook_secret: Optional[str] = None
    repos: Optional[list[str]] = None
    openrouter_model: Optional[str] = None
    openrouter_provider: Optional[str] = None


class RepoBody(BaseModel):
    repo: str


class BackfillBody(BaseModel):
    repo: str
    pages: int = 5


# ------------------------------------------------------------------ #
# API routes
# ------------------------------------------------------------------ #

@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.post("/api/review")
async def run_review(body: ReviewRequestBody):
    """Run an AI code review on the provided diff."""
    if not os.getenv("OPENROUTER_API_KEY"):
        raise HTTPException(status_code=400, detail="OPENROUTER_API_KEY not set")

    engine = get_engine()
    request = ReviewRequest(
        pr_number=body.pr_number,
        repo=body.repo,
        title=body.title,
        description=body.description,
        diff=body.diff,
        author=body.author,
        base_branch=body.base_branch,
        files_changed=body.files_changed,
    )

    try:
        result = engine.review(request)
        return {
            "pr_number": result.pr_number,
            "summary": result.summary,
            "approved": result.approved,
            "confidence": result.confidence,
            "issues": result.issues,
            "suggestions": result.suggestions,
            "past_decisions_applied": result.past_decisions_applied,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/decisions/search")
def search_decisions(body: SearchBody):
    """Search the decision store by semantic query, optionally scoped to a repo."""
    store = get_store()
    results = store.retrieve(body.query, k=body.k, repo=body.repo)
    return {"results": results, "count": len(results)}


@app.get("/api/decisions")
def list_decisions(k: int = 20, repo: Optional[str] = None):
    """List recent decisions from the store, optionally scoped to a repo."""
    store = get_store()
    results = store.retrieve("code review decision architecture pattern", k=k, repo=repo)
    return {"decisions": results, "count": len(results)}


@app.post("/api/decisions")
def add_decision(body: DecisionUpsertBody):
    """Manually add a decision (e.g. an ADR) to the store."""
    store = get_store()
    doc_id = f"manual-{body.ref.replace(' ', '-').replace('#', '').lower()}-{int(datetime.utcnow().timestamp())}"
    store.upsert(
        doc_id=doc_id,
        ref=body.ref,
        summary=body.summary,
        reasoning=body.reasoning,
        outcome=body.outcome,
        date=datetime.utcnow().isoformat(),
        metadata=body.metadata,
    )
    return {"doc_id": doc_id, "ref": body.ref}


@app.delete("/api/decisions/{doc_id}")
def delete_decision(doc_id: str):
    """Remove a decision from the store."""
    store = get_store()
    store.delete(doc_id)
    return {"deleted": doc_id}


@app.get("/api/stats")
def get_stats():
    """Return basic stats about the decision store."""
    store = get_store()
    # Sample to estimate count — guard against empty store
    try:
        results = store.retrieve("review decision", k=20)
        count = len(results)
    except Exception:
        count = 0
    return {
        "decisions_sampled": count,
        "backend": os.getenv("DECISION_STORE_BACKEND", "chroma"),
        "model": config_store.get_model(),
        "provider": config_store.get_provider(),
        "api_key_configured": bool(os.getenv("OPENROUTER_API_KEY")),
        "github_token_configured": bool(config_store.get_github_token()),
    }


# ------------------------------------------------------------------ #
# Settings & repositories (persisted server-side in config.json)
# ------------------------------------------------------------------ #

@app.get("/api/settings")
def get_settings():
    """Return GitHub settings. Secrets are reported as booleans, never echoed."""
    cfg = config_store.load_config()
    return {
        "repos": cfg.get("repos", []),
        "github_token_set": bool(config_store.get_github_token()),
        "webhook_secret_set": bool(config_store.get_webhook_secret()),
        # Model/provider are not secret — return the effective values.
        "openrouter_model": config_store.get_model(),
        "openrouter_provider": config_store.get_provider(),
    }


@app.put("/api/settings")
def update_settings(body: SettingsBody):
    """Update settings. Only provided (non-null) fields are changed."""
    update = {}
    if body.github_token is not None:
        update["github_token"] = body.github_token
    if body.webhook_secret is not None:
        update["webhook_secret"] = body.webhook_secret
    if body.repos is not None:
        update["repos"] = [r.strip() for r in body.repos if r and r.strip()]
    if body.openrouter_model is not None:
        update["openrouter_model"] = body.openrouter_model.strip()
    if body.openrouter_provider is not None:
        update["openrouter_provider"] = body.openrouter_provider.strip()
    if update:
        config_store.save_config(update)
    return get_settings()


@app.get("/api/repos")
def list_repos():
    """List the configured repositories."""
    return {"repos": config_store.get_repos()}


@app.post("/api/repos")
def add_repo(body: RepoBody):
    """Add a repository to the configured list."""
    repo = body.repo.strip()
    if "/" not in repo:
        raise HTTPException(status_code=400, detail="repo must be in 'owner/repo' form")
    return {"repos": config_store.add_repo(repo)}


@app.delete("/api/repos")
def delete_repo(repo: str):
    """Remove a repository from the configured list."""
    return {"repos": config_store.remove_repo(repo)}


@app.post("/api/backfill")
def backfill_repo(body: BackfillBody):
    """Import a repo's closed PRs into the decision store, server-side."""
    token = config_store.get_github_token()
    if not token:
        raise HTTPException(status_code=400, detail="GitHub token not configured")
    store = get_store()
    try:
        imported = run_backfill(body.repo, body.pages, token, store)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"repo": body.repo, "imported": imported}


@app.get("/api/repos/open-prs")
def open_prs(repo: str):
    """List a repo's open PRs that aren't yet in the decision store."""
    token = config_store.get_github_token()
    if not token:
        raise HTTPException(status_code=400, detail="GitHub token not configured")
    try:
        prs = list_open_prs(repo, token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    store = get_store()
    existing = store.existing_ids([pr_doc_id(repo, pr["number"]) for pr in prs])
    new_prs = [pr for pr in prs if pr_doc_id(repo, pr["number"]) not in existing]
    return {"repo": repo, "open_pr_count": len(prs), "new_prs": new_prs}


@app.get("/api/repos/prs")
def repo_prs(repo: str):
    """List a repo's PRs for the review picker — open PRs first, then closed,
    each group newest-updated first."""
    token = config_store.get_github_token()
    if not token:
        raise HTTPException(status_code=400, detail="GitHub token not configured")
    try:
        prs = list_prs(repo, token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    def recency(p):
        return p.get("updated_at") or ""

    open_prs = sorted((p for p in prs if p["state"] == "open"), key=recency, reverse=True)
    closed_prs = sorted((p for p in prs if p["state"] != "open"), key=recency, reverse=True)
    return {"repo": repo, "prs": open_prs + closed_prs}


@app.get("/api/repos/pr")
def repo_pr(repo: str, number: int):
    """Fetch one PR's metadata + diff, shaped for the review form."""
    token = config_store.get_github_token()
    if not token:
        raise HTTPException(status_code=400, detail="GitHub token not configured")
    try:
        return fetch_pr(repo, number, token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/balance")
async def get_balance():
    """Return the remaining credit balance for the configured OpenRouter key."""
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        return {"configured": False}

    import httpx

    headers = {"Authorization": f"Bearer {key}"}
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            resp = await client.get("https://openrouter.ai/api/v1/credits")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Could not reach OpenRouter: {e}")

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"OpenRouter returned {resp.status_code}",
        )

    data = resp.json().get("data", {})
    total = float(data.get("total_credits", 0) or 0)
    used = float(data.get("total_usage", 0) or 0)
    return {
        "configured": True,
        "balance": total - used,
        "total_credits": total,
        "total_usage": used,
        "currency": "USD",
    }


# ------------------------------------------------------------------ #
# GitHub webhook — auto-review new pull requests
# ------------------------------------------------------------------ #

def _verify_github_signature(body: bytes, signature: Optional[str]) -> bool:
    """Validate the X-Hub-Signature-256 header against the configured secret."""
    secret = config_store.get_webhook_secret()
    if not secret:
        # No secret configured — reject rather than accept unauthenticated calls.
        return False
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


async def _fetch_pr_diff(repo: str, pr_number: int) -> str:
    """Fetch a PR's unified diff from GitHub using GITHUB_TOKEN."""
    import httpx

    token = config_store.get_github_token()
    headers = {
        "Accept": "application/vnd.github.v3.diff",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


@app.post("/webhook/github")
async def github_webhook(request: Request):
    """Receive GitHub pull_request events and run an automatic review."""
    body = await request.body()
    if not _verify_github_signature(body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    event = request.headers.get("X-GitHub-Event", "")
    if event == "ping":
        return {"status": "pong"}
    if event != "pull_request":
        return {"status": "ignored", "event": event}

    payload = json.loads(body)
    if payload.get("action") not in ("opened", "synchronize", "reopened"):
        return {"status": "ignored", "action": payload.get("action")}

    pr = payload["pull_request"]
    repo = payload["repository"]["full_name"]
    pr_number = pr["number"]

    diff = await _fetch_pr_diff(repo, pr_number)

    engine = get_engine()
    request_obj = ReviewRequest(
        pr_number=pr_number,
        repo=repo,
        title=pr.get("title", ""),
        description=pr.get("body") or "",
        diff=diff,
        author=(pr.get("user") or {}).get("login", "unknown"),
        base_branch=(pr.get("base") or {}).get("ref", "main"),
        files_changed=[],
    )
    result = engine.review(request_obj)
    return {
        "pr_number": result.pr_number,
        "approved": result.approved,
        "confidence": result.confidence,
        "issue_count": len(result.issues),
    }


# ------------------------------------------------------------------ #
# Serve React frontend (production build)
# ------------------------------------------------------------------ #

FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def serve_frontend(full_path: str):
        # API routes are handled above; everything else gets index.html
        index = FRONTEND_DIST / "index.html"
        return FileResponse(str(index))
