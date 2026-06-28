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

import httpx

logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Depends, Query, Header, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel, Field

# Add parent dir so we can import the core modules
sys.path.insert(0, str(Path(__file__).parent.parent / "core"))

from backend.auth import require_user, require_admin
import rate_limit as _rate_limit

import config_store
import review_store
import review_jobs
import assessment_store
import compliance_analysis_store
import access_store
from decision_store import create_store, ChromaDecisionStore
from review_engine import CodeReviewEngine, ReviewRequest
from assessment_engine import AssessmentEngine, AssessmentRequest
import compliance_analysis
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
    compliance: bool = False
    agentic: bool = False
    agent_sources: list[str] = []


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
    llm_execution_mode: Optional[str] = None
    llm_worker_secret: Optional[str] = None
    llm_base_url: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_timeout_seconds: Optional[str] = None
    compliance_policies: Optional[dict] = None
    local_review_agents: Optional[list[dict]] = None
    local_agentic_targets: Optional[list[dict]] = None


class LlmTestBody(BaseModel):
    llm_base_url: Optional[str] = None
    llm_api_key: Optional[str] = None


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
    compliance: bool = False


class ComplianceSuggestionApplyBody(BaseModel):
    repo: str
    suggestion: dict


class ComplianceAnalyzeBody(BaseModel):
    repo: str
    limit: int = Field(default=50, ge=1, le=500)


class ComplianceIssueBody(BaseModel):
    title: Optional[str] = None
    body: str = ""
    agentic_target: Optional[str] = None


class WorkerClaimBody(BaseModel):
    worker_id: str = ""
    job_types: list[str] = []


class WorkerCompleteBody(BaseModel):
    result: dict


class WorkerErrorBody(BaseModel):
    error: str


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _token_for(repo: str) -> str:
    """Return the right GitHub token for a repo (owner-based routing).

    Raises 400 if no tokens are configured at all, and 403 if the repo is not
    explicitly configured for this app instance.
    """
    owner = repo.split("/")[0] if "/" in repo else repo
    token = config_store.get_token_for(owner)
    if not token:
        raise HTTPException(status_code=400, detail="GitHub token not configured")
    configured = {r.strip().lower() for r in config_store.get_repos() if isinstance(r, str)}
    if repo.strip().lower() not in configured:
        raise HTTPException(status_code=403, detail="Repository is not configured")
    return token


def _llm_execution_mode() -> str:
    return config_store.get_llm_execution_mode()


def _repo_compliance_enabled(repo: str) -> bool:
    return config_store.repo_requires_compliance_review(repo)


def _review_body_with_repo_defaults(body: ReviewRequestBody) -> ReviewRequestBody:
    return ReviewRequestBody(**{**body.model_dump(), "compliance": _repo_compliance_enabled(body.repo)})


def _assessment_body_with_repo_defaults(body: AssessmentRequestBody) -> AssessmentRequestBody:
    return AssessmentRequestBody(**{**body.model_dump(), "compliance": _repo_compliance_enabled(body.repo)})


def _require_worker_secret(provided: Optional[str]):
    expected = config_store.get_llm_worker_secret()
    if not expected:
        raise HTTPException(status_code=403, detail="LLM worker secret not configured")
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid worker secret")


def _require_local_queue_configured():
    if not config_store.get_llm_worker_secret():
        raise HTTPException(status_code=400, detail="LLM worker secret not configured for local_queue mode")


def _normalize_result_payload(result_payload: dict) -> dict:
    payload = dict(result_payload or {})
    payload["compliance_review"] = payload.get("compliance_review") or {}
    payload["agent_results"] = payload.get("agent_results") or []
    payload["agent_errors"] = payload.get("agent_errors") or []
    return payload


def _summarize_job(job: dict) -> dict:
    req = dict((job or {}).get("request") or {})
    diff = req.pop("diff", "")
    if job.get("job_type") == "compliance_followup":
        req_summary = {
            "repo": req.get("repo"),
            "analysis_id": req.get("analysis_id"),
            "issue_url": req.get("issue_url"),
            "target_id": req.get("target_id"),
            "agentic": bool(req.get("agentic")),
        }
        result = job.get("result")
        result_summary = None
        if isinstance(result, dict):
            result_summary = {
                "summary": result.get("summary"),
                "status": result.get("status"),
                "pr_url": result.get("pr_url"),
                "target": result.get("target"),
            }
        return {
            "id": job.get("id"),
            "job_type": job.get("job_type"),
            "executor": job.get("executor"),
            "status": job.get("status"),
            "claimed_by": job.get("claimed_by"),
            "started_at": job.get("started_at"),
            "completed_at": job.get("completed_at"),
            "created_at": job.get("created_at"),
            "updated_at": job.get("updated_at"),
            "error": job.get("error"),
            "request": req_summary,
            "result": result_summary,
        }
    req_summary = {
        "repo": req.get("repo"),
        "pr_number": req.get("pr_number"),
        "title": req.get("title"),
        "model": req.get("model"),
        "provider": req.get("provider"),
        "compliance": bool(req.get("compliance")),
        "agentic": bool(req.get("agentic")),
        "agent_sources": req.get("agent_sources") or [],
        "files_changed_count": len(req.get("files_changed") or []),
        "diff_lines": diff.count("\n") + 1 if diff else 0,
    }
    result = job.get("result")
    result_summary = None
    if isinstance(result, dict):
        result_summary = {
            "summary": result.get("summary"),
            "approved": result.get("approved"),
            "confidence": result.get("confidence"),
            "model": result.get("model"),
            "issues_count": len(result.get("issues") or []),
            "suggestions_count": len(result.get("suggestions") or []),
        }
    return {
        "id": job.get("id"),
        "job_type": job.get("job_type"),
        "executor": job.get("executor"),
        "status": job.get("status"),
        "claimed_by": job.get("claimed_by"),
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "error": job.get("error"),
        "request": req_summary,
        "result": result_summary,
    }


def _save_review_payload(request_payload: dict, result_payload: dict, source: str):
    """Persist a review payload without requiring a live ReviewResult object."""
    try:
        result_payload = _normalize_result_payload(result_payload)
        review_store.save_review({
            "repo": request_payload.get("repo"),
            "pr_number": result_payload.get("pr_number", request_payload.get("pr_number")),
            "title": request_payload.get("title"),
            "author": request_payload.get("author", "unknown"),
            "approved": result_payload.get("approved"),
            "confidence": result_payload.get("confidence"),
            "summary": result_payload.get("summary"),
            "issues": result_payload.get("issues") or [],
            "suggestions": result_payload.get("suggestions") or [],
            "past_decisions": result_payload.get("past_decisions_applied") or [],
            "compliance_review": result_payload.get("compliance_review") or {},
            "source": source,
            "model": result_payload.get("model"),
        })
    except Exception:
        pass


def _save_assessment_payload(result_payload: dict):
    try:
        result_payload = _normalize_result_payload(result_payload)
        assessment_store.save_assessment({
            "repo": result_payload.get("repo"),
            "summary": result_payload.get("summary"),
            "purpose": result_payload.get("purpose"),
            "tech_stack": result_payload.get("tech_stack") or [],
            "key_components": result_payload.get("key_components") or [],
            "vulnerabilities": result_payload.get("vulnerabilities") or [],
            "compliance_review": result_payload.get("compliance_review") or {},
            "model": result_payload.get("model"),
        })
    except Exception:
        logger.warning("Failed to persist worker assessment for %s", result_payload.get("repo"), exc_info=True)


def _persist_local_job_result(job: dict, result_payload: dict):
    if job.get("job_type") == "assessment":
        _save_assessment_payload(result_payload)
    elif job.get("job_type") == "review":
        _save_review_payload(job.get("request") or {}, result_payload, source="local_worker")


def _compliance_issue_title(analysis: dict) -> str:
    repo = analysis.get("repo") or "repo"
    health = (analysis.get("health") or {}).get("score")
    coverage = (analysis.get("coverage") or {}).get("coverage_score")
    parts = [f"Compliance follow-up for {repo}"]
    if health is not None or coverage is not None:
        suffix = []
        if health is not None:
            suffix.append(f"health {health}")
        if coverage is not None:
            suffix.append(f"coverage {coverage}")
        parts.append(f"({', '.join(suffix)})")
    return " ".join(parts)


def _compliance_issue_body(analysis: dict, analysis_id: int) -> str:
    repo = analysis.get("repo") or ""
    health = analysis.get("health") or {}
    coverage = analysis.get("coverage") or {}
    suggestions = analysis.get("suggestions") or []
    lines = [
        f"Compliance analysis follow-up for `{repo}`.",
        "",
        f"Saved analysis ID: `{analysis_id}`",
        "",
        "## Summary",
        f"- Policy health score: {health.get('score', 'n/a')}",
        f"- Coverage score: {coverage.get('coverage_score', 'n/a')}",
        f"- Policy suggestions: {len(suggestions)}",
        f"- Coverage blind spots: {len(coverage.get('blind_spots') or [])}",
    ]
    findings = health.get("findings") or []
    if findings:
        lines.extend(["", "## Policy Health Findings"])
        for item in findings[:10]:
            lines.append(f"- [{item.get('severity', 'info')}] {item.get('title', 'Finding')}: {item.get('recommendation') or item.get('evidence') or ''}".rstrip())
    blind_spots = coverage.get("blind_spots") or []
    if blind_spots:
        lines.extend(["", "## Coverage Blind Spots"])
        for item in blind_spots[:10]:
            lines.append(f"- [{item.get('severity', 'info')}] {item.get('category', 'category')}: {item.get('suggestion', '')}".rstrip())
    if suggestions:
        lines.extend(["", "## Suggested Policy Updates"])
        for item in suggestions[:10]:
            lines.append(f"- [{item.get('severity', 'info')}] {item.get('reason', item.get('type', 'Suggestion'))}")
    lines.extend([
        "",
        "## Raw Analysis",
        "```json",
        json.dumps({
            "health": health,
            "coverage": coverage,
            "suggestions": suggestions,
        }, indent=2),
        "```",
    ])
    return "\n".join(lines)


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
        compliance=body.compliance,
        agentic=body.agentic,
        agent_sources=body.agent_sources,
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
        "compliance_review": result.compliance_review,
        "model": result.model,
    }


@app.post("/api/review", dependencies=[Depends(require_review_quota)])
def create_review(body: ReviewRequestBody):
    """Enqueue an async review and return its job id.

    The model call can take long enough to exceed the serverless function limit
    if held in one request (a 504). Instead the client enqueues here, kicks off
    POST /api/review/{id}/run, and polls GET /api/review/{id} for the result.
    """
    executor = _llm_execution_mode()
    if body.agentic and executor != "local_queue":
        raise HTTPException(status_code=400, detail="Agentic review is available only in local_queue mode")
    if executor == "local_queue":
        _require_local_queue_configured()
    if executor == "inline" and not config_store.get_llm_api_key():
        raise HTTPException(status_code=400, detail="LLM API key not set")
    _token_for(body.repo)
    effective = _review_body_with_repo_defaults(body)
    job = review_jobs.create_job(effective.model_dump(), job_type="review", executor=executor)
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
    if job.get("job_type") != "review":
        raise HTTPException(status_code=404, detail="Review job not found")
    req = job.get("request") or {}
    _token_for(req.get("repo", ""))
    if job.get("executor") != "inline":
        return job
    if job["status"] != "queued":
        return job
    review_jobs.update_job(job_id, status="running")
    try:
        result = _execute_review(ReviewRequestBody(**req), source="api")
        return review_jobs.update_job(job_id, status="done", result=result)
    except Exception as e:
        return review_jobs.update_job(job_id, status="error", error=str(e))


@app.get("/api/review/{job_id}")
def get_review_job(job_id: str):
    """Poll a review job's status/result."""
    job = review_jobs.get_job(job_id)
    if not job or job.get("job_type") != "review":
        raise HTTPException(status_code=404, detail="Review job not found")
    req = job.get("request") or {}
    _token_for(req.get("repo", ""))
    if job.get("result"):
        job = dict(job)
        job["result"] = _normalize_result_payload(job["result"])
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
            "compliance_review": result.compliance_review,
            "source": source,
            "model": result.model,
        })
    except Exception:
        pass


@app.get("/api/reviews")
def list_review_history(repo: Optional[str] = None, pr_number: Optional[int] = None, limit: int = Query(default=50, ge=1, le=500)):
    """Return saved review runs (full history), newest first."""
    if repo:
        _token_for(repo)
    reviews = review_store.list_reviews(repo=repo, pr_number=pr_number, limit=limit)
    if not repo:
        allowed = []
        for review in reviews:
            review_repo = review.get("repo")
            if not review_repo:
                continue
            try:
                _token_for(review_repo)
            except HTTPException:
                continue
            allowed.append(review)
        reviews = allowed
    return {"reviews": [_normalize_result_payload(review) for review in reviews], "count": len(reviews)}


@app.post("/api/assessments", dependencies=[Depends(require_assessment_quota)])
def create_assessment(body: AssessmentRequestBody):
    """Enqueue an async project assessment and return its job id."""
    executor = _llm_execution_mode()
    if executor == "local_queue":
        _require_local_queue_configured()
    if executor == "inline" and not config_store.get_llm_api_key():
        raise HTTPException(status_code=400, detail="LLM API key not set")
    _token_for(body.repo)
    effective = _assessment_body_with_repo_defaults(body)
    payload = effective.model_dump()
    payload["_type"] = "assessment"
    job = review_jobs.create_job(payload, job_type="assessment", executor=executor)
    return {"id": job["id"], "status": job["status"]}


@app.post("/api/assessments/{job_id}/run")
def run_assessment_job(job_id: str):
    """Execute a queued assessment job."""
    job = review_jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Assessment job not found")
    if job.get("job_type") != "assessment":
        raise HTTPException(status_code=404, detail="Assessment job not found")
    if job.get("executor") != "inline":
        return job
    if job["status"] != "queued":
        return job
    req = job["request"]
    _token_for(req["repo"])
    review_jobs.update_job(job_id, status="running")
    try:
        result = _execute_assessment(AssessmentRequestBody(
            repo=req["repo"],
            model=req.get("model"),
            provider=req.get("provider"),
            compliance=req.get("compliance", False),
        ))
        return review_jobs.update_job(job_id, status="done", result=result)
    except Exception as e:
        return review_jobs.update_job(job_id, status="error", error=str(e))


@app.get("/api/assessments/{job_id}")
def get_assessment_job(job_id: str):
    """Poll an assessment job's status/result."""
    job = review_jobs.get_job(job_id)
    if not job or job.get("job_type") != "assessment":
        raise HTTPException(status_code=404, detail="Assessment job not found")
    if job.get("result"):
        job = dict(job)
        job["result"] = _normalize_result_payload(job["result"])
    return job


@app.get("/api/assessments")
def list_assessments_history(repo: Optional[str] = None, limit: int = Query(default=20, ge=1, le=100)):
    """Return saved assessments, newest first."""
    items = assessment_store.list_assessments(repo=repo, limit=limit)
    return {"assessments": [_normalize_result_payload(item) for item in items], "count": len(items)}


# ------------------------------------------------------------------ #
# Compliance analysis
# ------------------------------------------------------------------ #

@app.get("/api/compliance/dashboard")
def compliance_dashboard(repo: str, limit: int = Query(default=50, ge=1, le=500)):
    """Return a unified compliance dashboard: policy health, coverage, and suggestions."""
    _token_for(repo)
    try:
        return compliance_analysis.get_dashboard(repo, history_limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/compliance/health")
def compliance_health(repo: str):
    """Audit the configured HIPAA/HL7 policy against the repo's actual code/dependencies."""
    _token_for(repo)
    try:
        return compliance_analysis.get_health(repo)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/compliance/coverage")
def compliance_coverage_endpoint(repo: str, limit: int = Query(default=50, ge=1, le=500)):
    """Compare deterministic vs LLM compliance findings over time."""
    _token_for(repo)
    try:
        return compliance_analysis.get_coverage(repo, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/compliance/suggestions")
def compliance_suggestions(repo: str):
    """Return policy-update suggestions based on dependency/signal drift."""
    _token_for(repo)
    try:
        return {"suggestions": compliance_analysis.get_suggestions(repo)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/compliance/suggestions/apply", dependencies=[Depends(require_admin)])
def apply_compliance_suggestion(body: ComplianceSuggestionApplyBody):
    """Apply a single policy-update suggestion to the repo's compliance policy."""
    if "/" not in body.repo:
        raise HTTPException(status_code=400, detail="repo must be in 'owner/repo' form")
    try:
        updated = compliance_analysis.apply_suggestion(body.repo, body.suggestion)
        return {"compliance_policies": updated}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/compliance/analyze")
def analyze_compliance(body: ComplianceAnalyzeBody):
    """Run compliance analysis for a repo and persist the result."""
    _token_for(body.repo)
    try:
        dashboard = compliance_analysis.get_dashboard(body.repo, history_limit=body.limit)
        record = compliance_analysis_store.save_analysis(dashboard)
        return record
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/compliance/analyses/{analysis_id}/issue")
def create_compliance_issue(analysis_id: int, body: ComplianceIssueBody):
    """Create a GitHub issue from a saved compliance analysis, optionally enqueueing local agentic follow-up."""
    analysis = compliance_analysis_store.get_analysis(analysis_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="Compliance analysis not found")
    repo = analysis.get("repo")
    if not repo:
        raise HTTPException(status_code=400, detail="Saved analysis has no repo")
    token = _token_for(repo)
    target_id = (body.agentic_target or "").strip().lower()
    if target_id:
        if _llm_execution_mode() != "local_queue":
            raise HTTPException(status_code=400, detail="Agentic follow-up is available only in local_queue mode")
        _require_local_queue_configured()
        enabled_targets = {
            str(item.get("id") or "").strip().lower()
            for item in config_store.get_local_agentic_targets()
            if item.get("enabled")
        }
        if target_id not in enabled_targets:
            raise HTTPException(status_code=400, detail="Selected local agentic target is not enabled")
    title = (body.title or "").strip() or _compliance_issue_title(analysis)
    issue_body = (body.body or "").strip() or _compliance_issue_body(analysis, analysis_id)
    try:
        url = create_issue(repo, title, issue_body, token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    response = {"html_url": url}
    if target_id:
        job = review_jobs.create_job(
            {
                "repo": repo,
                "analysis_id": analysis_id,
                "issue_url": url,
                "issue_title": title,
                "issue_body": issue_body,
                "analysis": analysis,
                "target_id": target_id,
                "agentic": True,
            },
            job_type="compliance_followup",
            executor="local_queue",
        )
        response["job_id"] = job["id"]
        response["job_status"] = job["status"]
    return response


@app.get("/api/compliance/analyses")
def list_compliance_analyses(repo: Optional[str] = None, limit: int = Query(default=20, ge=1, le=100)):
    """Return persisted compliance analyses, newest first."""
    if repo:
        _token_for(repo)
    items = compliance_analysis_store.list_analyses(repo=repo, limit=limit)
    if not repo:
        allowed = []
        for item in items:
            item_repo = item.get("repo")
            if not item_repo:
                continue
            try:
                _token_for(item_repo)
            except HTTPException:
                continue
            allowed.append(item)
        items = allowed
    return {"analyses": items, "count": len(items)}


@app.get("/api/compliance/analyses/{analysis_id}")
def get_compliance_analysis(analysis_id: int):
    """Return one persisted compliance analysis by id."""
    item = compliance_analysis_store.get_analysis(analysis_id)
    if not item:
        raise HTTPException(status_code=404, detail="Compliance analysis not found")
    repo = item.get("repo")
    if not repo:
        raise HTTPException(status_code=400, detail="Saved analysis has no repo")
    _token_for(repo)
    return item


@app.post("/api/compliance/analyses/{analysis_id}/reanalyze")
def reanalyze_compliance(analysis_id: int):
    """Re-run compliance analysis for the repo of a saved analysis and persist the new result."""
    original = compliance_analysis_store.get_analysis(analysis_id)
    if not original:
        raise HTTPException(status_code=404, detail="Compliance analysis not found")
    repo = original.get("repo")
    if not repo:
        raise HTTPException(status_code=400, detail="Saved analysis has no repo")
    _token_for(repo)
    try:
        dashboard = compliance_analysis.get_dashboard(repo)
        record = compliance_analysis_store.save_analysis(dashboard)
        return record
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/queue")
def list_queue_jobs(
    limit: int = Query(default=100, ge=1, le=500),
    status: str = Query(default=""),
    job_type: str = Query(default=""),
):
    """List local-queue jobs newest-first with compact request/result summaries."""
    jobs = review_jobs.list_jobs(limit=limit, executor="local_queue", status=status.strip(), job_type=job_type.strip())
    return {"jobs": [_summarize_job(job) for job in jobs], "count": len(jobs)}


def _execute_assessment(body: AssessmentRequestBody) -> dict:
    engine = AssessmentEngine()
    request = AssessmentRequest(
        repo=body.repo,
        model=body.model or None,
        provider=body.provider or None,
        compliance=body.compliance,
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
            "compliance_review": result.compliance_review,
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
        "compliance_review": result.compliance_review,
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
        "llm_execution_mode": config_store.get_llm_execution_mode(),
        "llm_worker_secret_set": bool(config_store.get_llm_worker_secret()),
        "llm_base_url": config_store.get_llm_base_url(),
        "api_key_configured": bool(config_store.get_llm_api_key()),
        "llm_timeout_seconds": config_store.get_llm_timeout_seconds(),
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
        "llm_execution_mode": config_store.get_llm_execution_mode(),
        "llm_worker_secret_set": bool(config_store.get_llm_worker_secret()),
        "llm_base_url": config_store.get_llm_base_url(),
        "llm_api_key_set": bool(config_store.get_llm_api_key()),
        "llm_timeout_seconds": str(config_store.get_llm_timeout_seconds()),
        # Model list — not secret, return the effective resolved values.
        "openrouter_models": config_store.get_models(),
        # Legacy fields for backward compat with older frontend versions.
        "openrouter_model": config_store.get_model(),
        "openrouter_provider": config_store.get_provider(),
        "openrouter_model_2": config_store.get_model_2(),
        "openrouter_provider_2": config_store.get_provider_2(),
        "embedding_model": config_store.get_embedding_model(),
        "compliance_policies": config_store.get_compliance_policies(),
        "local_review_agents": config_store.get_local_review_agents(),
        "local_agentic_targets": config_store.get_local_agentic_targets(),
    }


@app.put("/api/settings", dependencies=[Depends(require_admin)])
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
    if body.llm_execution_mode is not None:
        mode = body.llm_execution_mode.strip().lower()
        if mode not in {"inline", "local_queue"}:
            raise HTTPException(status_code=400, detail="llm_execution_mode must be inline or local_queue")
        update["llm_execution_mode"] = mode
    if body.llm_worker_secret is not None:
        update["llm_worker_secret"] = body.llm_worker_secret.strip()
    if body.llm_base_url is not None:
        update["llm_base_url"] = body.llm_base_url.strip()
    if body.llm_api_key is not None:
        update["llm_api_key"] = body.llm_api_key.strip()
    if body.llm_timeout_seconds is not None:
        update["llm_timeout_seconds"] = body.llm_timeout_seconds.strip()
    if body.compliance_policies is not None:
        update["compliance_policies"] = body.compliance_policies
    if body.local_review_agents is not None:
        update["local_review_agents"] = body.local_review_agents
    if body.local_agentic_targets is not None:
        update["local_agentic_targets"] = body.local_agentic_targets
    if update:
        config_store.save_config(update)
    return get_settings()


@app.post("/worker/llm/claim")
def claim_llm_job(body: WorkerClaimBody, x_worker_secret: Optional[str] = Header(default=None, alias="X-Worker-Secret")):
    """Claim the next queued local-queue LLM job for an external worker."""
    wanted = [t for t in body.job_types if t in {"review", "assessment", "compliance_followup"}]
    _require_worker_secret(x_worker_secret)
    job = review_jobs.claim_next_job(job_types=wanted or None, executor="local_queue", worker_id=body.worker_id.strip())
    if not job:
        return Response(status_code=204)
    return job


@app.post("/worker/llm/{job_id}/complete")
def complete_llm_job(
    job_id: str,
    body: WorkerCompleteBody,
    x_worker_secret: Optional[str] = Header(default=None, alias="X-Worker-Secret"),
):
    """Store a local worker result and mark the job done."""
    job = review_jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="LLM job not found")
    if job.get("executor") != "local_queue":
        raise HTTPException(status_code=400, detail="Job is not configured for local queue execution")
    _require_worker_secret(x_worker_secret)
    if job.get("job_type") == "compliance_followup":
        return review_jobs.update_job(job_id, status="done", result=body.result)
    normalized = _normalize_result_payload(body.result)
    _persist_local_job_result(job, normalized)
    return review_jobs.update_job(job_id, status="done", result=normalized)


@app.post("/worker/llm/{job_id}/error")
def fail_llm_job(
    job_id: str,
    body: WorkerErrorBody,
    x_worker_secret: Optional[str] = Header(default=None, alias="X-Worker-Secret"),
):
    """Store a local worker failure and mark the job errored."""
    job = review_jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="LLM job not found")
    if job.get("executor") != "local_queue":
        raise HTTPException(status_code=400, detail="Job is not configured for local queue execution")
    _require_worker_secret(x_worker_secret)
    return review_jobs.update_job(job_id, status="error", error=body.error)


@app.get("/api/repos")
def list_repos():
    """List the configured repositories."""
    return {"repos": config_store.get_repos()}


@app.post("/api/repos", dependencies=[Depends(require_admin)])
def add_repo(body: RepoBody):
    """Add a repository to the configured list."""
    repo = body.repo.strip()
    if "/" not in repo:
        raise HTTPException(status_code=400, detail="repo must be in 'owner/repo' form")
    return {"repos": config_store.add_repo(repo)}


@app.delete("/api/repos", dependencies=[Depends(require_admin)])
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


@app.post("/api/github/tokens", dependencies=[Depends(require_admin)])
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


@app.delete("/api/github/tokens", dependencies=[Depends(require_admin)])
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


def _normalize_url(value: str) -> str:
    return (value or "").strip().rstrip("/")


def _llm_base_candidates(base_url: str) -> list[str]:
    candidates = [_normalize_url(base_url)]
    if candidates[0].endswith("/v1"):
        candidates.append(candidates[0][:-3])
    else:
        candidates.append(f"{candidates[0]}/v1")

    out = []
    for value in candidates:
        if value and value not in out:
            out.append(value)
    return out


async def _probe_llm_models_endpoint(client: httpx.AsyncClient, base_url: str) -> dict:
    url = f"{base_url}/models"
    try:
        resp = await client.get(url)
    except httpx.HTTPError as e:
        return {"base_url": base_url, "url": url, "ok": False, "error": str(e)}

    payload = None
    try:
        payload = resp.json()
    except ValueError:
        payload = None

    model_count = None
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        model_count = len(payload["data"])

    return {
        "base_url": base_url,
        "url": url,
        "ok": resp.status_code == 200 and isinstance(payload, dict),
        "status_code": resp.status_code,
        "body_preview": resp.text[:200],
        "model_count": model_count,
    }


@app.post("/api/llm/test")
async def test_llm_endpoint(body: LlmTestBody):
    base_url = _normalize_url(body.llm_base_url or config_store.get_llm_base_url())
    if not base_url:
        raise HTTPException(status_code=400, detail="LLM base URL is required")
    configured_base_url = _normalize_url(config_store.get_llm_base_url())
    if body.llm_api_key is not None:
        api_key = body.llm_api_key
    elif body.llm_base_url is None or base_url == configured_base_url:
        api_key = config_store.get_llm_api_key()
    else:
        api_key = ""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    candidates = _llm_base_candidates(base_url)

    async with httpx.AsyncClient(timeout=5.0, headers=headers) as client:
        results = [await _probe_llm_models_endpoint(client, candidate) for candidate in candidates]

    exact = results[0]
    alternate = next((item for item in results[1:] if item.get("ok")), None)

    if exact.get("ok"):
        return {
            "ok": True,
            "message": "LLM endpoint reachable.",
            "base_url": exact["base_url"],
            "models_url": exact["url"],
            "model_count": exact.get("model_count"),
        }

    if exact.get("status_code") in {401, 403}:
        return {
            "ok": False,
            "message": "LLM endpoint reachable, but the API key was rejected.",
            "base_url": exact["base_url"],
            "models_url": exact["url"],
            "suggested_base_url": exact["base_url"],
        }

    if alternate:
        if base_url.endswith("/v1"):
            message = "Configured URL did not work, but the root-style endpoint did. Remove /v1."
        else:
            message = "Configured URL did not work, but the /v1 endpoint did. Try adding /v1."
        return {
            "ok": False,
            "message": message,
            "base_url": exact["base_url"],
            "models_url": exact["url"],
            "suggested_base_url": alternate["base_url"],
            "suggested_models_url": alternate["url"],
            "model_count": alternate.get("model_count"),
        }

    if exact.get("status_code") == 404:
        detail = "Endpoint returned 404. This usually means the OpenAI-compatible path prefix is wrong."
    elif exact.get("error"):
        detail = f"Could not reach endpoint: {exact['error']}"
    else:
        detail = f"Endpoint returned {exact.get('status_code', 'an unknown error')}."

    return {
        "ok": False,
        "message": detail,
        "base_url": exact["base_url"],
        "models_url": exact["url"],
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
    token = _token_for(repo)
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
        compliance=_repo_compliance_enabled(repo),
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
