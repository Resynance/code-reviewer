"""
review_engine.py — LLM orchestration for AI code review.

Given a PR diff, the engine:
  1. Retrieves semantically-similar past decisions from the decision store.
  2. Builds a prompt that grounds the review in those decisions.
  3. Asks the model to return a structured review (issues, suggestions, verdict)
     via a forced function/tool call, so the output is always machine-parseable.

The model is served through OpenRouter (https://openrouter.ai), an
OpenAI-compatible gateway, so any model OpenRouter offers can be used by setting
OPENROUTER_MODEL. Authentication uses OPENROUTER_API_KEY.
"""

import os
import json
from dataclasses import dataclass, field
from typing import Optional

import config_store
import hipaa


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# How many past decisions to pull in as context for each review.
DEFAULT_CONTEXT_K = 6


@dataclass
class ReviewRequest:
    pr_number: int
    repo: str
    title: str
    diff: str
    description: str = ""
    author: str = "unknown"
    base_branch: str = "main"
    files_changed: list = field(default_factory=list)
    # Per-request model override. When set, takes precedence over the engine's
    # model_override and the config-store default, so the frontend can select
    # which configured model slot to run against.
    model: Optional[str] = None
    provider: Optional[str] = None
    hipaa: bool = False


@dataclass
class ReviewResult:
    pr_number: int
    summary: str
    approved: bool
    confidence: float
    issues: list = field(default_factory=list)
    suggestions: list = field(default_factory=list)
    past_decisions_applied: list = field(default_factory=list)
    model: str = ""
    hipaa_review: dict = field(default_factory=dict)


# JSON schema for the structured review. The model is forced to call the
# submit_review function with arguments matching this, guaranteeing a
# machine-parseable payload instead of free-form prose.
_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "One or two sentence overall assessment of the PR.",
        },
        "approved": {
            "type": "boolean",
            "description": "True if the PR is safe to merge as-is.",
        },
        "confidence": {
            "type": "number",
            "description": "Confidence in this assessment, from 0.0 to 1.0.",
        },
        "issues": {
            "type": "array",
            "description": "Concrete problems found in the diff.",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low"],
                    },
                    "file": {"type": "string"},
                    "description": {"type": "string"},
                    "suggestion": {"type": "string"},
                    "past_decision_ref": {
                        "type": "string",
                        "description": "Ref of a past decision that informed this, if any.",
                    },
                },
                "required": ["severity", "file", "description", "suggestion"],
            },
        },
        "suggestions": {
            "type": "array",
            "description": "Non-blocking improvements.",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [
                            "security",
                            "performance",
                            "architecture",
                            "style",
                            "test_coverage",
                        ],
                    },
                    "description": {"type": "string"},
                    "past_decision_ref": {"type": "string"},
                },
                "required": ["type", "description"],
            },
        },
        "past_decisions_applied": {
            "type": "array",
            "description": "Past decisions that meaningfully shaped this review.",
            "items": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string"},
                    "summary": {"type": "string"},
                    "how_applied": {
                        "type": "string",
                        "description": "How this past decision was applied to the current PR.",
                    },
                },
                "required": ["ref", "summary", "how_applied"],
            },
        },
        "hipaa_review": {
            "type": "object",
            "description": "HIPAA-focused findings. Use this only when HIPAA review mode is enabled.",
            "properties": {
                "hipaa_relevant": {"type": "boolean"},
                "requires_manual_compliance_review": {"type": "boolean"},
                "summary": {"type": "string"},
                "policy_notes_applied": {"type": "array", "items": {"type": "string"}},
                "hipaa_findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string"},
                            "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                            "title": {"type": "string"},
                            "evidence": {"type": "string"},
                            "recommendation": {"type": "string"},
                            "file": {"type": "string"},
                            "manual_review": {"type": "boolean"},
                        },
                        "required": ["category", "severity", "title", "evidence", "recommendation"],
                    },
                },
                "phi_exposure_risk": {"type": "array", "items": {"type": "object"}},
                "encryption_gaps": {"type": "array", "items": {"type": "object"}},
                "access_control_gaps": {"type": "array", "items": {"type": "object"}},
                "audit_trail_gaps": {"type": "array", "items": {"type": "object"}},
                "minimum_necessary_gaps": {"type": "array", "items": {"type": "object"}},
                "third_party_baa_risks": {"type": "array", "items": {"type": "object"}},
            },
        },
    },
    "required": ["summary", "approved", "confidence", "issues", "suggestions"],
}

# OpenAI-compatible function tool wrapping the schema above.
_REVIEW_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_review",
        "description": "Submit the structured result of a code review.",
        "parameters": _REVIEW_SCHEMA,
    },
}

_SYSTEM_PROMPT = """You are a senior code reviewer for an engineering team.
Review the pull request diff for correctness, security, performance, architecture,
and test coverage. Be specific and reference exact files.

You are given relevant past decisions the team has made on earlier PRs and ADRs.
When a past decision applies to the current change, follow that precedent and cite
its ref. Report it in past_decisions_applied so the author sees the connection.

Call the submit_review function exactly once with your structured findings. Set
approved=false if there is any critical or high-severity issue.

If HIPAA review mode is enabled, use the hipaa_review object for evidence-backed
HIPAA findings. Do not claim legal certification. Mark
requires_manual_compliance_review=true when the change is HIPAA-relevant but the
remaining decision depends on organizational process, BAA status, or deployment
controls that are not provable from code alone."""


class CodeReviewEngine:
    def __init__(self, store, model: Optional[str] = None, context_k: int = DEFAULT_CONTEXT_K):
        from openai import OpenAI

        self._store = store
        # Explicit override (mainly for tests). When None, the model and provider
        # are resolved from config_store per review, so UI changes take effect
        # without restarting the server.
        self._model_override = model
        self._context_k = context_k
        # OpenRouter is OpenAI-compatible; point the OpenAI SDK at its base URL.
        # The optional headers populate OpenRouter's app-attribution rankings.
        self._client = OpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=os.getenv("OPENROUTER_API_KEY"),
            default_headers={
                "HTTP-Referer": os.getenv("OPENROUTER_APP_URL", "http://localhost:1500"),
                "X-Title": "ReviewBot",
            },
            # Fail with a clear error inside the serverless function limit
            # (vercel.json maxDuration=300) rather than letting the platform kill
            # a slow model call with an opaque 504. max_retries=0 is essential:
            # the SDK's default retries would re-fire a timed-out call and push
            # total time past the limit anyway.
            timeout=240,
            max_retries=0,
        )

    def review(self, request: ReviewRequest) -> ReviewResult:
        decisions = self._retrieve_context(request)
        prompt = self._build_prompt(request, decisions)

        model = request.model or self._model_override or config_store.get_model()
        kwargs = dict(
            model=model,
            # Enough for a thorough structured review; kept modest so generation
            # finishes well inside the serverless function window.
            max_tokens=2500,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            tools=[_REVIEW_TOOL],
            tool_choice={"type": "function", "function": {"name": "submit_review"}},
        )

        # Optionally pin OpenRouter to a specific upstream provider.
        provider = request.provider if request.provider is not None else config_store.get_provider()
        if provider:
            kwargs["extra_body"] = {
                "provider": {"order": [provider], "allow_fallbacks": False}
            }

        response = self._client.chat.completions.create(**kwargs)

        payload = self._extract_tool_input(response)
        result = self._to_result(request, payload, decisions)
        result.model = model
        return result

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _retrieve_context(self, request: ReviewRequest):
        # Search the decision store using the PR's intent and touched files.
        query_parts = [request.title, request.description]
        query_parts.extend(request.files_changed or [])
        query = "\n".join(p for p in query_parts if p) or request.title
        try:
            # Ground the review in this repo's precedent plus global decisions.
            return self._store.retrieve(
                query,
                k=self._context_k,
                repo=request.repo or None,
                include_global=True,
            )
        except Exception:
            # A missing/empty store should never block a review.
            return []

    def _build_prompt(self, request: ReviewRequest, decisions) -> str:
        if decisions:
            decision_lines = []
            for d in decisions:
                decision_lines.append(
                    f"- {d.get('ref', '?')} ({d.get('outcome', 'unknown')}): "
                    f"{d.get('summary', '')}\n  reasoning: {d.get('reasoning', '')}"
                )
            decisions_block = "\n".join(decision_lines)
        else:
            decisions_block = "(no relevant past decisions found)"

        files = ", ".join(request.files_changed) or "(not specified)"
        hipaa_section = ""
        if request.hipaa:
            policy = config_store.get_hipaa_policy(request.repo)
            deterministic = hipaa.review_findings(request.diff, request.files_changed, policy)
            hipaa_section = hipaa.prompt_section(policy, deterministic)
        return (
            f"# Pull Request #{request.pr_number} — {request.repo}\n"
            f"Title: {request.title}\n"
            f"Author: {request.author}\n"
            f"Base branch: {request.base_branch}\n"
            f"Files changed: {files}\n\n"
            f"## Description\n{request.description or '(none)'}\n\n"
            f"## Relevant past decisions\n{decisions_block}\n"
            f"{hipaa_section}"
            f"\n## Diff\n```diff\n{request.diff}\n```\n"
        )

    def _extract_tool_input(self, response) -> dict:
        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None) or []
        for call in tool_calls:
            if call.function.name == "submit_review":
                # Function arguments arrive as a JSON string.
                return json.loads(call.function.arguments)
        raise RuntimeError("Model did not return a submit_review function call.")

    def _to_result(self, request: ReviewRequest, payload: dict, decisions) -> ReviewResult:
        # Enrich applied decisions with the stored summary when the model omitted it.
        by_ref = {d.get("ref"): d for d in decisions if d.get("ref")}
        applied = []
        for entry in payload.get("past_decisions_applied", []) or []:
            ref = entry.get("ref", "")
            applied.append(
                {
                    "ref": ref,
                    "summary": entry.get("summary") or by_ref.get(ref, {}).get("summary", ""),
                    "how_applied": entry.get("how_applied", ""),
                }
            )

        confidence = float(payload.get("confidence", 0.0) or 0.0)
        confidence = max(0.0, min(1.0, confidence))

        deterministic_hipaa = None
        if request.hipaa:
            deterministic_hipaa = hipaa.review_findings(
                request.diff,
                request.files_changed,
                config_store.get_hipaa_policy(request.repo),
            )
        hipaa_review = hipaa.normalize_result(
            payload.get("hipaa_review"),
            deterministic_hipaa,
            enabled=request.hipaa,
        )
        issues = payload.get("issues", []) or []
        issues.extend(hipaa.review_issue_overlays(hipaa_review, enabled=request.hipaa))
        deduped_issues = []
        seen_issues = set()
        for issue in issues:
            key = (issue.get("severity"), issue.get("file"), issue.get("description"))
            if key not in seen_issues:
                seen_issues.add(key)
                deduped_issues.append(issue)

        return ReviewResult(
            pr_number=request.pr_number,
            summary=payload.get("summary", ""),
            approved=bool(payload.get("approved", False)) and not any(
                i.get("severity") in {"critical", "high"} for i in deduped_issues
            ),
            confidence=confidence,
            issues=deduped_issues,
            suggestions=payload.get("suggestions", []) or [],
            past_decisions_applied=applied,
            hipaa_review=hipaa_review,
        )
