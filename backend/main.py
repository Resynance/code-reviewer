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
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel, Field

from backend.auth import require_user, require_admin
import rate_limit as _rate_limit

# Add parent dir so we can import the core modules
sys.path.insert(0, str(Path(__file__).parent.parent / "core"))

import config_store
import review_store
import review_jobs
import assessment_store
import access_store
from decision_store import create_store, ChromaDecisionStore
from review_engine import CodeReviewEngine, ReviewRequest
from assessment_engine import AssessmentEngine, AssessmentRequest
from github_backfill import (
    backfill as run_backfill,
    list_open_prs,
    list_prs,
    fetch_pr,
    pr_doc_id,
    list_owners,
    list_owner_repos,
    post_pr_comment,
    create_issue,
)


# ------------------------------------------------------------------ #
# Rate limiting
# ------------------------------------------------------------------ #

def _rate_limit_key(request: Request) -> str:
    """User email from JWT, or remote IP as fallback when auth is disabled."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            from backend.auth import _decode
            claims = _decode(auth[len("Bearer "):])
            return claims.get("email") or "anon"
        except Exception:
            pass
    return request.client.host if request.client else "anon"


async def require_review_quota(request: Request):
    key = _rate_limit_key(request)
    try:
        allowed, retry_after = _rate_limit.check(f"review:{key}")
    except Exception:
        return  # fail open — a limiter bug must not take down the API
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Try again in {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )


async def require_assessment_quota(request: Request):
    key = _rate_limit_key(request)
    try:
        allowed, retry_after = _rate_limit.check(f"assess:{key}")
    except Exception:
        return  # fail open — a limiter bug must not take down the API
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Try again in {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )


# ------------------------------------------------------------------ #
# App setup
# ------------------------------------------------------------------ #

_in_production = bool(os.environ.get("VERCEL") or os.environ.get("DISABLE_DOCS"))

app = FastAPI(
    title="Code Review Tool API",
    version="1.0.0",
    # No-op unless SUPABASE_JWT_SECRET is set (gates /api/* with Supabase Auth).
    dependencies=[Depends(require_user)],
    # Disable interactive API docs in production to avoid exposing the full
    # schema (all endpoint names, parameters, models) to unauthenticated callers.
    docs_url=None if _in_production else "/docs",
    redoc_url=None if _in_production else "/redoc",
    openapi_url=None if _in_production else "/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Surface database connectivity failures as a clear 503 instead of an opaque 500.
# The Postgres-backed stores raise psycopg.OperationalError when the DB is
# unreachable (wrong DATABASE_URL, paused Supabase project, or pooler credentials
# mid-rotation). Auth survives such an outage via the ALLOWED_EMAILS bootstrap
# (see auth.py), so without this every DB-backed endpoint would 500 with no hint.
# Guarded import: the file backend (local/dev) has no psycopg and never raises it.
try:
    import psycopg

    @app.exception_handler(psycopg.OperationalError)
    async def _db_unavailable(request: Request, exc: psycopg.OperationalError):
        return JSONResponse(
            status_code=503,
            content={"detail": "Database unavailable — check DATABASE_URL and Supabase status."},
        )
except ImportError:
    pass


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
    # Optional per-review model override (frontend sends the slug of the chosen slot).
    model: Optional[str] = None
    provider: Optional[str] = None
    hipaa: bool = False


class DecisionUpsertBody(BaseModel):
    ref: str
    summary: str
    reasoning: str
    outcome: str
    metadata: dict = {}


class SearchBody(BaseModel):
    query: str
    k: int = Field(default=10, ge=1, le=100)
    repo: Optional[str] = None


class ModelSlot(BaseModel):
    label: str = ""
    model: str
    provider: str = ""


class SettingsBody(BaseModel):
    # All optional: only provided (non-null) fields are updated. Send "" to clear
    # a value (falls back to env/default); omit/null to leave it unchanged.
    github_token: Optional[str] = None
    webhook_secret: Optional[str] = None
    repos: Optional[list[str]] = None
    openrouter_models: Optional[list[ModelSlot]] = None
    # Legacy fields — still accepted so old clients don't break.
    openrouter_model: Optional[str] = None
    openrouter_provider: Optional[str] = None
    openrouter_model_2: Optional[str] = None
    openrouter_provider_2: Optional[str] = None
    embedding_model: Optional[str] = None
    hipaa_policies: Optional[dict] = None


class RepoBody(BaseModel):
    repo: str


class EmailBody(BaseModel):
    email: str


class PrCommentBody(BaseModel):
    repo: str
    pr_number: int
    body: str


class CreateIssueBody(BaseModel):
    repo: str
    title: str
    body: str = ""


class BackfillBody(BaseModel):
    repo: str
    pages: int = 5


class AddTokenBody(BaseModel):
    token: str


class AssessmentRequestBody(BaseModel):
    repo: str
    model: Optional[str] = None
    provider: Optional[str] = None
    hipaa: bool = False


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _token_for(repo: str) -> str:
    """Return the right GitHub token for a repo (owner-based routing).

    Raises 400 if no tokens are configured at all.
    """
    owner = repo.split("/")[0] if "/" in repo else repo
    token = config_store.get_token_for(owner)
    if not token:
        raise HTTPException(status_code=400, detail="GitHub token not configured")
    return token


# ------------------------------------------------------------------ #
# API routes
# ------------------------------------------------------------------ #

@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


def _execute_review(body: ReviewRequestBody, source: str) -> dict:
    """Run the review synchronously and return the response payload.

    Shared by the async job runner and the GitHub webhook so the review logic
    lives in one place.
    """
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
        model=body.model or None,
        provider=body.provider or None,
        hipaa=body.hipaa,
    )
    result = engine.review(request)
    _save_review(request, result, source=source)
    return {
        "pr_number": result.pr_number,
        "summary": result.summary,
        "approved": result.approved,
        "confidence": result.confidence,
        "issues": result.issues,
        "suggestions": result.suggestions,
        "past_decisions_applied": result.past_decisions_applied,
        "hipaa_review": result.hipaa_review,
        "model": result.model,
    }


@app.post("/api/review", dependencies=[Depends(require_review_quota)])
def create_review(body: ReviewRequestBody):
    """Enqueue an async review and return its job id.

    The model call can take long enough to exceed the serverless function limit
    if held in one request (a 504). Instead the client enqueues here, kicks off
    POST /api/review/{id}/run, and polls GET /api/review/{id} for the result.
    """
    if not os.getenv("OPENROUTER_API_KEY"):
        raise HTTPException(status_code=400, detail="OPENROUTER_API_KEY not set")
    job = review_jobs.create_job(body.model_dump())
    return {"id": job["id"], "status": job["status"]}


@app.post("/api/review/{job_id}/run")
def run_review_job(job_id: str):
    """Execute a queued review job, persisting the outcome to the job record.

    Called by the client right after enqueue. The work runs in this request, but
    the result is written to the job so polling recovers it even if this
    connection drops. Idempotent: a job already running/finished is returned as-is.
    """
    job = review_jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Review job not found")
    if job["status"] != "queued":
        return job
    review_jobs.update_job(job_id, status="running")
    try:
        result = _execute_review(ReviewRequestBody(**job["request"]), source="api")
        return review_jobs.update_job(job_id, status="done", result=result)
    except Exception as e:
        return review_jobs.update_job(job_id, status="error", error=str(e))


@app.get("/api/review/{job_id}")
def get_review_job(job_id: str):
    """Poll a review job's status/result."""
    job = review_jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Review job not found")
    return job


def _save_review(request, result, source):
    """Persist a review run (best-effort — never fail the review on a save error)."""
    try:
        review_store.save_review({
            "repo": request.repo,
            "pr_number": request.pr_number,
            "title": request.title,
            "author": request.author,
            "approved": result.approved,
            "confidence": result.confidence,
            "summary": result.summary,
            "issues": result.issues,
            "suggestions": result.suggestions,
            "past_decisions": result.past_decisions_applied,
            "hipaa_review": result.hipaa_review,
            "source": source,
            "model": result.model,
        })
    except Exception:
        pass


@app.get("/api/reviews")
def list_review_history(repo: Optional[str] = None, pr_number: Optional[int] = None, limit: int = Query(default=50, ge=1, le=500)):
    """Return saved review runs (full history), newest first."""
    reviews = review_store.list_reviews(repo=repo, pr_number=pr_number, limit=limit)
    return {"reviews": reviews, "count": len(reviews)}


@app.post("/api/assessments", dependencies=[Depends(require_assessment_quota)])
def create_assessment(body: AssessmentRequestBody):
    """Enqueue an async project assessment and return its job id."""
    if not os.getenv("OPENROUTER_API_KEY"):
        raise HTTPException(status_code=400, detail="OPENROUTER_API_KEY not set")
    payload = body.model_dump()
    payload["_type"] = "assessment"
    job = review_jobs.create_job(payload)
    return {"id": job["id"], "status": job["status"]}


@app.post("/api/assessments/{job_id}/run")
def run_assessment_job(job_id: str):
    """Execute a queued assessment job."""
    job = review_jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Assessment job not found")
    if job["status"] != "queued":
        return job
    review_jobs.update_job(job_id, status="running")
    try:
        req = job["request"]
        result = _execute_assessment(AssessmentRequestBody(
            repo=req["repo"],
            model=req.get("model"),
            provider=req.get("provider"),
            hipaa=req.get("hipaa", False),
        ))
        return review_jobs.update_job(job_id, status="done", result=result)
    except Exception as e:
        return review_jobs.update_job(job_id, status="error", error=str(e))


@app.get("/api/assessments/{job_id}")
def get_assessment_job(job_id: str):
    """Poll an assessment job's status/result."""
    job = review_jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Assessment job not found")
    return job


@app.get("/api/assessments")
def list_assessments_history(repo: Optional[str] = None, limit: int = Query(default=20, ge=1, le=100)):
    """Return saved assessments, newest first."""
    items = assessment_store.list_assessments(repo=repo, limit=limit)
    return {"assessments": items, "count": len(items)}


def _execute_assessment(body: AssessmentRequestBody) -> dict:
    engine = AssessmentEngine()
    request = AssessmentRequest(
        repo=body.repo,
        model=body.model or None,
        provider=body.provider or None,
        hipaa=body.hipaa,
    )
    result = engine.assess(request)
    try:
        assessment_store.save_assessment({
            "repo": result.repo,
            "summary": result.summary,
            "purpose": result.purpose,
            "tech_stack": result.tech_stack,
            "key_components": result.key_components,
            "vulnerabilities": result.vulnerabilities,
            "hipaa_review": result.hipaa_review,
            "model": result.model,
        })
    except Exception:
        logger.warning("Failed to persist assessment for %s", result.repo, exc_info=True)
    return {
        "repo": result.repo,
        "summary": result.summary,
        "purpose": result.purpose,
        "tech_stack": result.tech_stack,
        "key_components": result.key_components,
        "vulnerabilities": result.vulnerabilities,
        "hipaa_review": result.hipaa_review,
        "model": result.model,
    }


@app.post("/api/pr-comment")
def pr_comment(body: PrCommentBody):
    """Post selected review findings as a comment on the PR."""
    token = _token_for(body.repo)
    try:
        url = post_pr_comment(body.repo, body.pr_number, body.body, token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"html_url": url}


@app.post("/api/issue")
def open_issue(body: CreateIssueBody):
    """Open a new GitHub issue from selected review findings."""
    token = _token_for(body.repo)
    try:
        url = create_issue(body.repo, body.title, body.body, token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"html_url": url}


@app.post("/api/decisions/search")
def search_decisions(body: SearchBody):
    """Search the decision store by semantic query, optionally scoped to a repo."""
    store = get_store()
    results = store.retrieve(body.query, k=body.k, repo=body.repo)
    return {"results": results, "count": len(results)}


@app.get("/api/decisions")
def list_decisions(k: int = Query(default=20, ge=1, le=200), repo: Optional[str] = None):
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
        "embedding_model": config_store.get_embedding_model(),
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
    tokens = config_store.get_github_tokens()
    return {
        "repos": cfg.get("repos", []),
        "github_token_set": bool(config_store.get_github_token()),
        "github_tokens": [{"username": t["username"], "orgs": t.get("orgs", [])} for t in tokens],
        "webhook_secret_set": bool(config_store.get_webhook_secret()),
        # Model list — not secret, return the effective resolved values.
        "openrouter_models": config_store.get_models(),
        # Legacy fields for backward compat with older frontend versions.
        "openrouter_model": config_store.get_model(),
        "openrouter_provider": config_store.get_provider(),
        "openrouter_model_2": config_store.get_model_2(),
        "openrouter_provider_2": config_store.get_provider_2(),
        "embedding_model": config_store.get_embedding_model(),
        "hipaa_policies": config_store.get_hipaa_policies(),
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
    if body.openrouter_models is not None:
        update["openrouter_models"] = [
            {"label": s.label.strip(), "model": s.model.strip(), "provider": s.provider.strip()}
            for s in body.openrouter_models if s.model.strip()
        ]
    # Legacy fields — accepted but not surfaced in the UI any more.
    if body.openrouter_model is not None:
        update["openrouter_model"] = body.openrouter_model.strip()
    if body.openrouter_provider is not None:
        update["openrouter_provider"] = body.openrouter_provider.strip()
    if body.openrouter_model_2 is not None:
        update["openrouter_model_2"] = body.openrouter_model_2.strip()
    if body.openrouter_provider_2 is not None:
        update["openrouter_provider_2"] = body.openrouter_provider_2.strip()
    if body.embedding_model is not None:
        update["embedding_model"] = body.embedding_model.strip()
    if body.hipaa_policies is not None:
        update["hipaa_policies"] = body.hipaa_policies
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


@app.get("/api/access")
def list_access():
    """List emails on the runtime allowlist (who may use the app)."""
    return {"emails": access_store.list_emails()}


@app.post("/api/access", dependencies=[Depends(require_admin)])
def add_access(body: EmailBody):
    """Grant a user access by email (takes effect within ~30s, no redeploy)."""
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="invalid email")
    return {"emails": access_store.add_email(email)}


@app.delete("/api/access", dependencies=[Depends(require_admin)])
def remove_access(email: str):
    """Revoke a user's access by email."""
    return {"emails": access_store.remove_email(email)}


@app.post("/api/github/tokens")
def add_github_token(body: AddTokenBody):
    """Add a GitHub token — auto-discovers the account username and org memberships."""
    if not body.token.strip():
        raise HTTPException(status_code=400, detail="Token is required")
    try:
        owners = list_owners(body.token.strip())
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not verify token with GitHub: {e}")
    user = next((o for o in owners if o["type"] == "user"), None)
    if not user:
        raise HTTPException(status_code=502, detail="Could not determine GitHub username from token")
    username = user["login"]
    orgs = [o["login"] for o in owners if o["type"] == "org"]
    tokens = config_store.add_github_token(username, orgs, body.token.strip())
    return {"github_tokens": [{"username": t["username"], "orgs": t.get("orgs", [])} for t in tokens]}


@app.delete("/api/github/tokens")
def remove_github_token(username: str):
    """Remove a GitHub token by username."""
    tokens = config_store.remove_github_token(username)
    return {"github_tokens": [{"username": t["username"], "orgs": t.get("orgs", [])} for t in tokens]}


@app.get("/api/github/owners")
def github_owners():
    """List owner accounts across all configured tokens for repo discovery."""
    tokens = config_store.get_github_tokens()
    if not tokens:
        # Legacy: fall back to single-token env/config
        token = config_store.get_github_token()
        if not token:
            raise HTTPException(status_code=400, detail="No GitHub tokens configured")
        tokens = [{"username": "", "orgs": [], "token": token}]

    seen: dict = {}
    last_err = None
    for entry in tokens:
        try:
            for owner in list_owners(entry["token"]):
                if owner["login"] not in seen:
                    seen[owner["login"]] = owner
        except Exception as e:
            last_err = e

    if not seen and last_err:
        raise HTTPException(status_code=502, detail=str(last_err))
    return {"owners": list(seen.values())}


@app.get("/api/github/repos")
def github_owner_repos(owner: str, type: str = "org"):
    """List repos under an owner using the token that has access to that owner."""
    token = config_store.get_token_for(owner)
    if not token:
        raise HTTPException(status_code=400, detail="No GitHub token configured")
    try:
        return {"owner": owner, "repos": list_owner_repos(owner, token, owner_type=type)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/backfill")
def backfill_repo(body: BackfillBody):
    """Import a repo's closed PRs into the decision store, server-side."""
    token = _token_for(body.repo)
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
    token = _token_for(repo)
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
    token = _token_for(repo)
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
    token = _token_for(repo)
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
    expected = "sha256=" + hmac.HMAC(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


async def _fetch_pr_diff(repo: str, pr_number: int) -> str:
    """Fetch a PR's unified diff from GitHub."""
    import httpx

    token = config_store.get_token_for(repo.split("/")[0]) or ""
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
    _save_review(request_obj, result, source="webhook")
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

# On Vercel the SPA is served by the CDN, not this function — skip static serving
# (the bundle is API-only and the filesystem has no frontend/dist).
if not os.getenv("VERCEL") and FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def serve_frontend(full_path: str):
        # API routes are handled above; everything else gets index.html
        index = FRONTEND_DIST / "index.html"
        return FileResponse(str(index))
